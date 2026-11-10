"""catalogue, locations, volunteers and labels

The tables are generated from models.py. The rules a single row cannot show
are the app's (inventory_tng.locations, volunteers, categories, items and
labels).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-01 07:52:12.598711
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"
# The columns the app may change once a row is made; the rest are set as it
# is made, never after. So a label's code and what it names never change,
# a package never moves to another item, and a place keeps its kind and its
# volunteer: rules that then need no check.
UPDATABLE = {
    "label": ("revoked_at",),
    "item_package": ("name", "size", "retired_at"),
    "location": ("name", "parent_id", "retired_at"),
}


def upgrade() -> None:
    # For ix_item_search. Trusted, so the database's owner may create it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
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
        sa.Column("old_row", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_row", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "action IN ('insert', 'update', 'delete')",
            name=op.f("ck_catalogue_history_action"),
        ),
        sa.CheckConstraint(
            "actor ~ '^(admin|volunteer):[0-9]+$'",
            name=op.f("ck_catalogue_history_actor"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalogue_history")),
    )
    op.create_index(
        "ix_catalogue_history_table_name_row_id",
        "catalogue_history",
        ["table_name", "row_id"],
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_category")),
    )
    op.create_index(
        "uq_category_name",
        "category",
        [sa.literal_column("lower(name)")],
        unique=True,
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
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "search_text",
            sa.Text(),
            sa.Computed(
                "name || ' ' || coalesce(manufacturer, '') || ' ' || "
                "coalesce(part_number, '') || ' ' || coalesce(old_code, '')",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.CheckConstraint("unit IN ('each', 'm', 'ft')", name=op.f("ck_item_unit")),
        sa.CheckConstraint("minimum >= 0", name=op.f("ck_item_minimum")),
        sa.CheckConstraint("on_order >= 0", name=op.f("ck_item_on_order")),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["category.id"],
            name=op.f("fk_item_category_id_category"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item")),
    )
    op.create_index(
        "ix_item_search",
        "item",
        ["search_text"],
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
    op.create_index(
        "uq_item_name",
        "item",
        [sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_index(
        "uq_item_old_code",
        "item",
        ["old_code"],
        unique=True,
        postgresql_where=sa.text("old_code IS NOT NULL"),
    )
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
    op.create_index(
        "uq_location_custody_name",
        "location",
        [sa.literal_column("lower(name)")],
        unique=True,
        postgresql_where=sa.text("kind = 'custody' AND retired_at IS NULL"),
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
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
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
        "uq_item_package_name",
        "item_package",
        [sa.literal_column("item_id"), sa.literal_column("lower(name)")],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
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
    op.create_index("uq_label_code", "label", ["code"], unique=True)
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
    # Foreign keys, for RESTRICT and the rules' lookups.
    for table, column in (
        ("item", "category_id"),
        ("item_package", "item_id"),
        ("label", "item_id"),
        ("label", "location_id"),
        ("label", "package_id"),
        ("location", "parent_id"),
        ("location", "volunteer_id"),
        ("vendor_offer", "item_id"),
        ("vendor_offer", "vendor_id"),
        ("vendor_price", "vendor_offer_id"),
        ("volunteer", "merged_into_id"),
    ):
        op.create_index(op.f(f"ix_{table}_{column}"), table, [column])
    # History is only added to by the app (history.py), never rewritten.
    op.execute(f"REVOKE UPDATE, DELETE ON catalogue_history FROM {APP_ROLE}")
    for table, columns in UPDATABLE.items():
        op.execute(f"REVOKE UPDATE ON {table} FROM {APP_ROLE}")
        op.execute(f"GRANT UPDATE ({', '.join(columns)}) ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    # Indexes go with their tables. pg_trgm stays: something
    # else may use it, and it may have been there before this migration.
    for table in (
        "vendor_price",
        "label",
        "vendor_offer",
        "item_package",
        "location",
        "item",
        "volunteer",
        "vendor",
        "category",
        "catalogue_history",
    ):
        op.drop_table(table)
