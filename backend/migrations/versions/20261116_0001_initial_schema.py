"""initial schema: every table, what the app's login may do to each, and
the rows the app starts with

The tables are made from models.py, whose CHECKs and indexes hold the rules
one row can show; the rest are the app's. The owner makes them, and the
app's login (DEPLOYMENT.md, Database roles) is given SELECT, INSERT, UPDATE
and DELETE on each by default, never TRUNCATE. GRANTS then takes some back:

- History, the ledger and activity are only read and added to, so each is
  its own history.
- Over-recorded checks and the settings rows are never deleted.
- Some tables are updated only in the columns listed, so the rest are set
  as a row is made and never after: a label's code and what it names, a
  package's item, a place's kind and volunteer, an open package's key and
  what it was opened from, an alias's item and name.

It also makes each settings table's one row, with its defaults
(db.settings_row), and the adjustment reasons the app starts with, those
it gives itself among them (adjustment_reasons.seeded).

Ali, 2026-11-17: made as one migration from the many before it.

Revision ID: 0001
Revises:
Create Date: 2026-11-16 23:36:53.976625
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "inventory_app"
DEFAULT_PRIVILEGES = "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
# The privileges taken back from what the app's login is given by default,
# and the columns it may update where UPDATE is taken back.
GRANTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "activity": ("UPDATE, DELETE", ()),
    "catalogue_history": ("UPDATE, DELETE", ()),
    "ledger_transaction": ("UPDATE, DELETE", ()),
    "movement": ("UPDATE, DELETE", ()),
    "over_recorded": ("DELETE", ()),
    "scan_settings": ("DELETE", ()),
    "sign_in_settings": ("DELETE", ()),
    "site_settings": ("DELETE", ()),
    "label": ("UPDATE", ("revoked_at",)),
    "item_package": (
        "UPDATE",
        (
            "name",
            "size",
            "retired_at",
            "track_opened",
            "short_below",
            "minimum_sealed",
            "edit_version",
            "search_text",
            "folded_with",
            "name_key",
            "unit_key",
            "name_shown",
        ),
    ),
    "location": (
        "UPDATE",
        (
            "name",
            "parent_id",
            "retired_at",
            "edit_version",
            "time_zone",
            "search_text",
            "folded_with",
        ),
    ),
    "open_package": (
        "UPDATE",
        (
            "location_id",
            "reported_at",
            "reported_by_volunteer_id",
            "closed_at",
            "closed_by_admin_id",
            "length_reported_at",
            "length_reported_by_volunteer_id",
            "length_reported",
            "length_reported_in_id",
            "below_zero_at",
        ),
    ),
    "item_alias": ("UPDATE", ("retired_at", "unit_key", "name_shown")),
}
# names.name_key_sql's, written out, so the CHECKs keep the rule they were
# made with when names.py changes; by code point, as ruff refuses the
# look-alikes themselves.
# names._LOOK_ALIKE's code points, by the ASCII character each is read as.
LOOK_ALIKE = {
    " ": (0x00A0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000),
    "'": (0x00B4, 0x02BC, *range(0x2018, 0x201C), 0x2032, 0x2035, 0xFF07),
    '"': (*range(0x201C, 0x2020), 0x2033, 0x2036, 0xFF02),
    "-": (0x00AD, *range(0x2010, 0x2016), 0x2212, 0xFE58, 0xFE63, 0xFF0D),
}
LOOK_ALIKES = "".join(chr(code) for codes in LOOK_ALIKE.values() for code in codes)
# Each one's ASCII counterpart, quoted for SQL.
PLAIN = "".join(plain * len(codes) for plain, codes in LOOK_ALIKE.items()).replace(
    "'", "''"
)
IGNORED = "'(?!(?<=[0-9])[.][0-9])[\\t-\\r -/:-@[-`{-~]'"
NAME_KEY = (
    'lower(regexp_replace(translate(name COLLATE "C",'
    f" '{LOOK_ALIKES}', '{PLAIN}'), {IGNORED}, '', 'g'))"
)
SETTINGS = ("scan_settings", "sign_in_settings", "site_settings")
REASONS = (
    "Damaged",
    "Lost or stolen",
    "Found",
    "Used for testing",
    "Records were wrong",
    "Used up",
    "Recorded more than moved",
)
# Newest first, so each goes before the tables it refers to.
TABLES = (
    "over_recorded",
    "movement",
    "vendor_price",
    "open_package",
    "label",
    "vendor_offer",
    "ledger_transaction",
    "item_package",
    "item_alias",
    "reset_link",
    "recovery_code",
    "reading_rule",
    "location",
    "item",
    "device",
    "authenticator",
    "admin_session",
    "admin_password",
    "activity",
    "volunteer",
    "vendor",
    "site_settings",
    "sign_in_settings",
    "sent_form",
    "scan_settings",
    "registration_code",
    "rate_limit",
    "category",
    "catalogue_history",
    "admin",
    "adjustment_reason",
)


def upgrade() -> None:
    # For the trigram indexes searches use. Trusted, so the database's owner
    # may create it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # Before any table is made, so each is given these.
    op.execute(
        f"{DEFAULT_PRIVILEGES} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES"
        f" TO {APP_ROLE}"
    )
    op.create_table(
        "adjustment_reason",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "edit_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_adjustment_reason")),
    )
    op.create_index(
        "uq_adjustment_reason_name",
        "adjustment_reason",
        [sa.literal_column("lower(name)")],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
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
        sa.Column(
            "must_set_up_totp",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
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
        "catalogue_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("row_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=True),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "old_row",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "new_row",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.CheckConstraint(
            "action IN ('insert', 'update', 'delete')",
            name=op.f("ck_catalogue_history_action"),
        ),
        sa.CheckConstraint(
            "actor ~ '^((admin|volunteer):[0-9]+|system:[a-z_]+)$'",
            name=op.f("ck_catalogue_history_actor"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalogue_history")),
    )
    op.create_index(
        "ix_catalogue_history_table_name_row_id",
        "catalogue_history",
        ["table_name", "row_id"],
        unique=False,
    )
    op.create_table(
        "category",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "edit_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_category")),
    )
    op.create_index(
        "uq_category_name",
        "category",
        [sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "rate_limit",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("hits", postgresql.ARRAY(sa.DateTime(timezone=True)), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_rate_limit")),
    )
    op.create_index(
        "ix_rate_limit_expires_at", "rate_limit", ["expires_at"], unique=False
    )
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
        sa.Column(
            "edit_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_scan_settings_one_row")),
        sa.CheckConstraint(
            "name_check_minutes BETWEEN 0 AND 1440",
            name=op.f("ck_scan_settings_name_check_minutes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_settings")),
    )
    op.create_table(
        "sent_form",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("key", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sent_form")),
        sa.UniqueConstraint("key", name=op.f("uq_sent_form_key")),
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
        sa.Column(
            "account_change_minutes",
            sa.Integer(),
            server_default=sa.text("30"),
            nullable=False,
        ),
        sa.Column(
            "edit_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint(
            "totp_policy IN ('required', 'optional')",
            name=op.f("ck_sign_in_settings_totp_policy"),
        ),
        sa.CheckConstraint(
            "account_change_minutes BETWEEN 0 AND 60",
            name=op.f("ck_sign_in_settings_account_change_minutes"),
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
        sa.Column(
            "edit_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_site_settings_one_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_site_settings")),
    )
    op.create_table(
        "vendor",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vendor")),
    )
    op.create_index(
        "uq_vendor_name",
        "vendor",
        [sa.literal_column("lower(name)")],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.create_table(
        "volunteer",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("merged_into_id", sa.Integer(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("folded_with", sa.Text(), nullable=False),
        sa.Column(
            "edit_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint(
            "merged_into_id <> id", name=op.f("ck_volunteer_not_merged_into_self")
        ),
        sa.CheckConstraint(
            "merged_into_id IS NULL OR retired_at IS NOT NULL",
            name=op.f("ck_volunteer_merged_is_retired"),
        ),
        sa.ForeignKeyConstraint(
            ["merged_into_id"],
            ["volunteer.id"],
            name=op.f("fk_volunteer_merged_into_id_volunteer"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_volunteer")),
    )
    op.create_index(
        op.f("ix_volunteer_merged_into_id"),
        "volunteer",
        ["merged_into_id"],
        unique=False,
    )
    op.create_index(
        "ix_volunteer_search",
        "volunteer",
        ["search_text"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )
    op.create_index(
        "uq_volunteer_email",
        "volunteer",
        [sa.literal_column("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.create_index(
        "uq_volunteer_name",
        "volunteer",
        [sa.literal_column("lower(name)")],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.create_table(
        "activity",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("by_admin_id", sa.Integer(), nullable=True),
        sa.Column("admin_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('invited', 'sign_in_reset', 'retired', 'restored', 'recovery_link_made', 'sign_in_settings_changed', 'locked_down', 'let_in', 'authenticators_retired')",
            name=op.f("ck_activity_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["admin.id"],
            name=op.f("fk_activity_admin_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["by_admin_id"],
            ["admin.id"],
            name=op.f("fk_activity_by_admin_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activity")),
    )
    op.create_index(
        op.f("ix_activity_admin_id"), "activity", ["admin_id"], unique=False
    )
    op.create_index("ix_activity_at", "activity", ["at"], unique=False)
    op.create_index(
        op.f("ix_activity_by_admin_id"), "activity", ["by_admin_id"], unique=False
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
        sa.Column("account_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_in_before", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("sealed_key", sa.LargeBinary(), nullable=True),
        sa.Column("key_version", sa.SmallInteger(), nullable=True),
        sa.Column("last_step", sa.BigInteger(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(sealed_key IS NULL) = (retired_at IS NOT NULL) AND (key_version IS NULL) = (sealed_key IS NULL)",
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
        sa.Column(
            "edit_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
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
    op.create_table(
        "item",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("manufacturer", sa.Text(), nullable=True),
        sa.Column("part_number", sa.Text(), nullable=True),
        sa.Column("old_code", sa.Text(), nullable=True),
        sa.Column("unit", sa.Text(), server_default="each", nullable=False),
        sa.Column("minimum", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("on_order", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("ordering_notes", sa.Text(), nullable=True),
        sa.Column(
            "track_opened",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name_folded", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("unit_key", sa.Text(), nullable=False),
        sa.Column("name_shown", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("folded_with", sa.Text(), nullable=False),
        sa.Column(
            "edit_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint("unit IN ('each', 'm', 'ft')", name=op.f("ck_item_unit")),
        sa.CheckConstraint("minimum >= 0", name=op.f("ck_item_minimum")),
        sa.CheckConstraint(f"name_key = {NAME_KEY}", name=op.f("ck_item_name_key")),
        sa.CheckConstraint("on_order >= 0", name=op.f("ck_item_on_order")),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["category.id"],
            name=op.f("fk_item_category_id_category"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item")),
    )
    op.create_index(op.f("ix_item_category_id"), "item", ["category_id"], unique=False)
    op.create_index(
        "ix_item_search",
        "item",
        ["search_text"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )
    op.create_index(
        "uq_item_manufacturer_part_number",
        "item",
        [
            sa.literal_column("lower(manufacturer)"),
            sa.literal_column("lower(part_number)"),
        ],
        unique=True,
        postgresql_where=sa.text(
            "manufacturer IS NOT NULL AND part_number IS NOT NULL"
        ),
    )
    op.create_index("uq_item_name", "item", ["name_key"], unique=True)
    op.create_index(
        "uq_item_old_code",
        "item",
        ["old_code"],
        unique=True,
        postgresql_where=sa.text("old_code IS NOT NULL"),
    )
    op.create_index("uq_item_unit_key", "item", ["unit_key"], unique=True)
    op.create_table(
        "location",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("volunteer_id", sa.Integer(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_zone", sa.Text(), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("folded_with", sa.Text(), nullable=False),
        sa.Column(
            "edit_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint(
            "(kind = 'area') = (parent_id IS NOT NULL)",
            name=op.f("ck_location_area_has_parent"),
        ),
        sa.CheckConstraint(
            "(kind = 'custody') = (volunteer_id IS NOT NULL)",
            name=op.f("ck_location_custody_has_volunteer"),
        ),
        sa.CheckConstraint(
            "kind IN ('store', 'area', 'vehicle', 'custody')",
            name=op.f("ck_location_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["location.id"],
            name=op.f("fk_location_parent_id_location"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["volunteer_id"],
            ["volunteer.id"],
            name=op.f("fk_location_volunteer_id_volunteer"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_location")),
    )
    op.create_index(
        op.f("ix_location_parent_id"), "location", ["parent_id"], unique=False
    )
    op.create_index(
        op.f("ix_location_volunteer_id"), "location", ["volunteer_id"], unique=False
    )
    op.create_index(
        "uq_location_custody_name",
        "location",
        [sa.literal_column("lower(name)")],
        unique=True,
        postgresql_where=sa.text("kind = 'custody' AND retired_at IS NULL"),
    )
    op.create_index(
        "uq_location_custody_volunteer_id",
        "location",
        ["volunteer_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'custody' AND retired_at IS NULL"),
    )
    op.create_index(
        "uq_location_name",
        "location",
        [
            sa.literal_column("kind"),
            sa.literal_column("coalesce(parent_id, 0)"),
            sa.literal_column("lower(name)"),
        ],
        unique=True,
        postgresql_where=sa.text("kind <> 'custody'"),
    )
    op.create_table(
        "reading_rule",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("reading", sa.Text(), nullable=True),
        sa.Column(
            "respell", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("decided_by_admin_id", sa.Integer(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "edit_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint(
            "reading IS NOT NULL OR NOT respell",
            name=op.f("ck_reading_rule_respell_a_unit"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_admin_id"],
            ["admin.id"],
            name=op.f("fk_reading_rule_decided_by_admin_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reading_rule")),
    )
    op.create_index(
        op.f("ix_reading_rule_decided_by_admin_id"),
        "reading_rule",
        ["decided_by_admin_id"],
        unique=False,
    )
    op.create_index("uq_reading_rule_token", "reading_rule", ["token"], unique=True)
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
        sa.Column("recovery_token_hash", sa.LargeBinary(), nullable=True),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(recovery_token_hash IS NOT NULL) = (kind = 'recovery')",
            name=op.f("ck_reset_link_recovery"),
        ),
        sa.CheckConstraint(
            "kind IN ('invite', 'emailed', 'reset', 'recovery')",
            name=op.f("ck_reset_link_kind"),
        ),
        sa.CheckConstraint(
            "made_by_id IS NULL OR kind IN ('invite', 'reset')",
            name=op.f("ck_reset_link_made_by"),
        ),
        sa.CheckConstraint(
            "made_by_id <> admin_id", name=op.f("ck_reset_link_not_made_for_oneself")
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
        sa.UniqueConstraint(
            "recovery_token_hash", name=op.f("uq_reset_link_recovery_token_hash")
        ),
        sa.UniqueConstraint("token_hash", name=op.f("uq_reset_link_token_hash")),
    )
    op.create_index(
        op.f("ix_reset_link_admin_id"), "reset_link", ["admin_id"], unique=False
    )
    op.create_index(
        op.f("ix_reset_link_made_by_id"), "reset_link", ["made_by_id"], unique=False
    )
    op.create_table(
        "item_alias",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name_folded", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("unit_key", sa.Text(), nullable=False),
        sa.Column("name_shown", sa.Text(), nullable=False),
        sa.CheckConstraint(
            f"name_key = {NAME_KEY}", name=op.f("ck_item_alias_name_key")
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["item.id"],
            name=op.f("fk_item_alias_item_id_item"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_alias")),
    )
    op.create_index(
        op.f("ix_item_alias_item_id"), "item_alias", ["item_id"], unique=False
    )
    op.create_index(
        "uq_item_alias_name",
        "item_alias",
        ["name_key"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.create_index(
        "uq_item_alias_unit_key",
        "item_alias",
        ["unit_key"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.create_table(
        "item_package",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("size", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("track_opened", sa.Boolean(), nullable=True),
        sa.Column("short_below", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("minimum_sealed", sa.Integer(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("unit_key", sa.Text(), nullable=False),
        sa.Column("name_shown", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("folded_with", sa.Text(), nullable=False),
        sa.Column(
            "edit_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint(
            "minimum_sealed >= 0", name=op.f("ck_item_package_minimum_sealed")
        ),
        sa.CheckConstraint(
            f"name_key = {NAME_KEY}", name=op.f("ck_item_package_name_key")
        ),
        sa.CheckConstraint(
            "short_below < size", name=op.f("ck_item_package_short_below_under_size")
        ),
        sa.CheckConstraint("short_below > 0", name=op.f("ck_item_package_short_below")),
        sa.CheckConstraint("size > 0", name=op.f("ck_item_package_size")),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["item.id"],
            name=op.f("fk_item_package_item_id_item"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_package")),
    )
    op.create_index(
        op.f("ix_item_package_item_id"), "item_package", ["item_id"], unique=False
    )
    op.create_index(
        "uq_item_package_name", "item_package", ["item_id", "name_key"], unique=True
    )
    op.create_index(
        "uq_item_package_unit_key", "item_package", ["item_id", "unit_key"], unique=True
    )
    op.create_table(
        "ledger_transaction",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("from_location_id", sa.Integer(), nullable=True),
        sa.Column("to_location_id", sa.Integer(), nullable=True),
        sa.Column("install_number", sa.Integer(), nullable=True),
        sa.Column("volunteer_id", sa.Integer(), nullable=True),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("admin_id", sa.Integer(), nullable=True),
        sa.Column("reason_id", sa.Integer(), nullable=True),
        sa.Column("time_zone", sa.Text(), nullable=False),
        sa.Column(
            "used_up",
            postgresql.ARRAY(sa.Uuid()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "lengths",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'adjustment') = (reason_id IS NOT NULL)",
            name=op.f("ck_ledger_transaction_adjustment_reason"),
        ),
        sa.CheckConstraint(
            "(kind IN ('count', 'adjustment')) = (admin_id IS NOT NULL)",
            name=op.f("ck_ledger_transaction_admin_counts"),
        ),
        sa.CheckConstraint(
            "CASE kind WHEN 'move' THEN num_nulls(from_location_id, to_location_id) = 0 WHEN 'open' THEN from_location_id IS NOT NULL AND from_location_id = to_location_id WHEN 'receipt' THEN from_location_id IS NULL AND to_location_id IS NOT NULL WHEN 'install' THEN from_location_id IS NOT NULL AND to_location_id IS NULL ELSE num_nulls(from_location_id, to_location_id) = 1 END",
            name=op.f("ck_ledger_transaction_sides"),
        ),
        sa.CheckConstraint(
            "from_location_id <> to_location_id OR kind = 'open'",
            name=op.f("ck_ledger_transaction_two_places"),
        ),
        sa.CheckConstraint(
            "install_number IS NULL OR kind = 'install'",
            name=op.f("ck_ledger_transaction_install_number_on_install"),
        ),
        sa.CheckConstraint(
            "kind IN ('move', 'receipt', 'install', 'count', 'adjustment', 'open', 'report')",
            name=op.f("ck_ledger_transaction_kind"),
        ),
        sa.CheckConstraint(
            "install_number > 0", name=op.f("ck_ledger_transaction_install_number")
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["admin.id"],
            name=op.f("fk_ledger_transaction_admin_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["device.id"],
            name=op.f("fk_ledger_transaction_device_id_device"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["from_location_id"],
            ["location.id"],
            name=op.f("fk_ledger_transaction_from_location_id_location"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reason_id"],
            ["adjustment_reason.id"],
            name=op.f("fk_ledger_transaction_reason_id_adjustment_reason"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_location_id"],
            ["location.id"],
            name=op.f("fk_ledger_transaction_to_location_id_location"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["volunteer_id"],
            ["volunteer.id"],
            name=op.f("fk_ledger_transaction_volunteer_id_volunteer"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_transaction")),
        sa.UniqueConstraint(
            "idempotency_key", name=op.f("uq_ledger_transaction_idempotency_key")
        ),
    )
    op.create_index(
        op.f("ix_ledger_transaction_admin_id"),
        "ledger_transaction",
        ["admin_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_transaction_device_id"),
        "ledger_transaction",
        ["device_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_transaction_from_location_id"),
        "ledger_transaction",
        ["from_location_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_transaction_reason_id"),
        "ledger_transaction",
        ["reason_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_transaction_to_location_id"),
        "ledger_transaction",
        ["to_location_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_transaction_volunteer_id"),
        "ledger_transaction",
        ["volunteer_id"],
        unique=False,
    )
    op.create_table(
        "vendor_offer",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("vendor_id", sa.Integer(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("vendor_sku", sa.Text(), nullable=True),
        sa.Column("units_per_order", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "units_per_order > 0", name=op.f("ck_vendor_offer_units_per_order")
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["item.id"],
            name=op.f("fk_vendor_offer_item_id_item"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendor.id"],
            name=op.f("fk_vendor_offer_vendor_id_vendor"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vendor_offer")),
    )
    op.create_index(
        op.f("ix_vendor_offer_item_id"), "vendor_offer", ["item_id"], unique=False
    )
    op.create_index(
        op.f("ix_vendor_offer_vendor_id"), "vendor_offer", ["vendor_id"], unique=False
    )
    op.create_table(
        "label",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("package_id", sa.Integer(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "code ~ '^[23456789ABCDEFGHJKMNPQRSTVWXYZ]{10}$'",
            name=op.f("ck_label_code"),
        ),
        sa.CheckConstraint(
            "num_nonnulls(item_id, location_id) = 1",
            name=op.f("ck_label_item_or_location"),
        ),
        sa.CheckConstraint(
            "package_id IS NULL OR item_id IS NOT NULL",
            name=op.f("ck_label_package_needs_item"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["item.id"],
            name=op.f("fk_label_item_id_item"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["location.id"],
            name=op.f("fk_label_location_id_location"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["item_package.id"],
            name=op.f("fk_label_package_id_item_package"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_label")),
    )
    op.create_index(op.f("ix_label_item_id"), "label", ["item_id"], unique=False)
    op.create_index(
        op.f("ix_label_location_id"), "label", ["location_id"], unique=False
    )
    op.create_index(op.f("ix_label_package_id"), "label", ["package_id"], unique=False)
    op.create_index("uq_label_code", "label", ["code"], unique=True)
    op.create_index(
        "uq_label_current",
        "label",
        [
            sa.literal_column("coalesce(item_id, 0)"),
            sa.literal_column("coalesce(package_id, 0)"),
            sa.literal_column("coalesce(location_id, 0)"),
        ],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "open_package",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("key", sa.Uuid(), nullable=False),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("package_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_by_volunteer_id", sa.Integer(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("length_reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("length_reported_by_volunteer_id", sa.Integer(), nullable=True),
        sa.Column("length_reported", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("length_reported_in_id", sa.Integer(), nullable=True),
        sa.Column("below_zero_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(closed_at IS NULL) = (closed_by_admin_id IS NULL)",
            name=op.f("ck_open_package_closed_by"),
        ),
        sa.CheckConstraint(
            "(reported_at IS NULL) = (reported_by_volunteer_id IS NULL)",
            name=op.f("ck_open_package_reported_by"),
        ),
        sa.CheckConstraint(
            "length_reported >= 0", name=op.f("ck_open_package_length_reported")
        ),
        sa.CheckConstraint(
            "num_nulls(length_reported_at, length_reported_by_volunteer_id, length_reported, length_reported_in_id) IN (0, 4)",
            name=op.f("ck_open_package_length_reported_by"),
        ),
        sa.ForeignKeyConstraint(
            ["closed_by_admin_id"],
            ["admin.id"],
            name=op.f("fk_open_package_closed_by_admin_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["item.id"],
            name=op.f("fk_open_package_item_id_item"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["length_reported_by_volunteer_id"],
            ["volunteer.id"],
            name=op.f("fk_open_package_length_reported_by_volunteer_id_volunteer"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["length_reported_in_id"],
            ["ledger_transaction.id"],
            name=op.f("fk_open_package_length_reported_in_id_ledger_transaction"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["location.id"],
            name=op.f("fk_open_package_location_id_location"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["item_package.id"],
            name=op.f("fk_open_package_package_id_item_package"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reported_by_volunteer_id"],
            ["volunteer.id"],
            name=op.f("fk_open_package_reported_by_volunteer_id_volunteer"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_open_package")),
        sa.UniqueConstraint("key", name=op.f("uq_open_package_key")),
    )
    op.create_index(
        op.f("ix_open_package_closed_by_admin_id"),
        "open_package",
        ["closed_by_admin_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_open_package_item_id"), "open_package", ["item_id"], unique=False
    )
    op.create_index(
        op.f("ix_open_package_length_reported_by_volunteer_id"),
        "open_package",
        ["length_reported_by_volunteer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_open_package_length_reported_in_id"),
        "open_package",
        ["length_reported_in_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_open_package_location_id"),
        "open_package",
        ["location_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_open_package_package_id"), "open_package", ["package_id"], unique=False
    )
    op.create_index(
        op.f("ix_open_package_reported_by_volunteer_id"),
        "open_package",
        ["reported_by_volunteer_id"],
        unique=False,
    )
    op.create_table(
        "vendor_price",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("vendor_offer_id", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("price >= 0", name=op.f("ck_vendor_price_price")),
        sa.ForeignKeyConstraint(
            ["vendor_offer_id"],
            ["vendor_offer.id"],
            name=op.f("fk_vendor_price_vendor_offer_id_vendor_offer"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vendor_price")),
    )
    op.create_index(
        op.f("ix_vendor_price_vendor_offer_id"),
        "vendor_price",
        ["vendor_offer_id"],
        unique=False,
    )
    op.create_table(
        "movement",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("from_package_id", sa.Integer(), nullable=True),
        sa.Column("from_open_package_id", sa.Integer(), nullable=True),
        sa.Column("to_package_id", sa.Integer(), nullable=True),
        sa.Column("to_open_package_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(from_package_id, from_open_package_id) <= 1",
            name=op.f("ck_movement_one_form_leaving"),
        ),
        sa.CheckConstraint(
            "num_nonnulls(to_package_id, to_open_package_id) <= 1",
            name=op.f("ck_movement_one_form_arriving"),
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_movement_quantity")),
        sa.ForeignKeyConstraint(
            ["from_open_package_id"],
            ["open_package.id"],
            name=op.f("fk_movement_from_open_package_id_open_package"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["from_package_id"],
            ["item_package.id"],
            name=op.f("fk_movement_from_package_id_item_package"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["item.id"],
            name=op.f("fk_movement_item_id_item"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_open_package_id"],
            ["open_package.id"],
            name=op.f("fk_movement_to_open_package_id_open_package"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_package_id"],
            ["item_package.id"],
            name=op.f("fk_movement_to_package_id_item_package"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["ledger_transaction.id"],
            name=op.f("fk_movement_transaction_id_ledger_transaction"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_movement")),
    )
    op.create_index(
        op.f("ix_movement_from_open_package_id"),
        "movement",
        ["from_open_package_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_movement_from_package_id"),
        "movement",
        ["from_package_id"],
        unique=False,
    )
    op.create_index(op.f("ix_movement_item_id"), "movement", ["item_id"], unique=False)
    op.create_index(
        op.f("ix_movement_to_open_package_id"),
        "movement",
        ["to_open_package_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_movement_to_package_id"), "movement", ["to_package_id"], unique=False
    )
    op.create_index(
        "uq_movement_line",
        "movement",
        [
            "transaction_id",
            "item_id",
            sa.literal_column("coalesce(from_package_id, 0)"),
            sa.literal_column("coalesce(from_open_package_id, 0)"),
            sa.literal_column("coalesce(to_package_id, 0)"),
            sa.literal_column("coalesce(to_open_package_id, 0)"),
        ],
        unique=True,
    )
    op.create_table(
        "over_recorded",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "raised_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("movement_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("settled_how", sa.Text(), nullable=True),
        sa.Column("checked_reason", sa.Text(), nullable=True),
        sa.Column("checked_words", sa.Text(), nullable=True),
        sa.Column("left_count_id", sa.Integer(), nullable=True),
        sa.Column("arrived_count_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "(checked_words IS NULL) = (checked_reason IS DISTINCT FROM 'other') AND checked_words ~ '[^[:space:]]' AND length(checked_words) <= 200",
            name=op.f("ck_over_recorded_checked_words"),
        ),
        sa.CheckConstraint(
            "checked_reason IS NULL OR settled_how = 'checked' AND checked_reason IN ('left', 'arrived', 'both', 'other')",
            name=op.f("ck_over_recorded_checked_reason"),
        ),
        sa.CheckConstraint(
            "settled_how IN ('corrected', 'checked', 'counted')",
            name=op.f("ck_over_recorded_how"),
        ),
        sa.CheckConstraint(
            "settled_how IS DISTINCT FROM 'counted' OR num_nonnulls(left_count_id, arrived_count_id) >= 1",
            name=op.f("ck_over_recorded_counted"),
        ),
        sa.CheckConstraint(
            "num_nulls(settled_at, settled_by_admin_id, settled_how) IN (0, 3)",
            name=op.f("ck_over_recorded_settled"),
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_over_recorded_quantity")),
        sa.CheckConstraint(
            "settled_at IS NOT NULL OR num_nonnulls(left_count_id, arrived_count_id) <= 1",
            name=op.f("ck_over_recorded_counted_waiting"),
        ),
        sa.ForeignKeyConstraint(
            ["arrived_count_id"],
            ["ledger_transaction.id"],
            name=op.f("fk_over_recorded_arrived_count_id_ledger_transaction"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["left_count_id"],
            ["ledger_transaction.id"],
            name=op.f("fk_over_recorded_left_count_id_ledger_transaction"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["movement_id"],
            ["movement.id"],
            name=op.f("fk_over_recorded_movement_id_movement"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settled_by_admin_id"],
            ["admin.id"],
            name=op.f("fk_over_recorded_settled_by_admin_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_over_recorded")),
        sa.UniqueConstraint("movement_id", name=op.f("uq_over_recorded_movement_id")),
    )
    op.create_index(
        op.f("ix_over_recorded_arrived_count_id"),
        "over_recorded",
        ["arrived_count_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_over_recorded_left_count_id"),
        "over_recorded",
        ["left_count_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_over_recorded_settled_by_admin_id"),
        "over_recorded",
        ["settled_by_admin_id"],
        unique=False,
    )

    for table, (revoked, columns) in GRANTS.items():
        op.execute(f"REVOKE {revoked} ON {table} FROM {APP_ROLE}")
        if columns:
            op.execute(f"GRANT UPDATE ({', '.join(columns)}) ON {table} TO {APP_ROLE}")
    for table in SETTINGS:
        op.execute(f"INSERT INTO {table} DEFAULT VALUES")
    reasons = sa.table("adjustment_reason", sa.column("name", sa.Text))
    op.bulk_insert(reasons, [{"name": name} for name in REASONS])


def downgrade() -> None:
    for table in TABLES:
        op.drop_table(table)
    op.execute(
        f"{DEFAULT_PRIVILEGES} REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES"
        f" FROM {APP_ROLE}"
    )
