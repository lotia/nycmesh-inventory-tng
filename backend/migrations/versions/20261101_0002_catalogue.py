"""catalogue, locations, volunteers and labels

The tables are generated from models.py. The rules a single row cannot show
are triggers, below; each raises a check violation (23514) named for its rule.

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


# Written into this file, not imported, so the migration stays as it was run.
HISTORY_TABLES = (
    "category",
    "item",
    "item_package",
    "vendor",
    "vendor_offer",
    "vendor_price",
    "volunteer",
    "location",
    "label",
)

FUNCTIONS = r"""
-- Every trigger that reads other rows calls this first, so rule-checking
-- writes go one at a time: two concurrent ones could each pass their checks
-- and together break them, such as two moves that make a cycle. Writes are
-- few (administrators, and volunteers taking stock), so one lock is enough.
CREATE FUNCTION lock_catalogue_rules() RETURNS void
LANGUAGE sql AS $$
    SELECT pg_advisory_xact_lock(hashtext('inventory.catalogue_rules'));
$$;

CREATE FUNCTION record_catalogue_history() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO catalogue_history (table_name, row_id, action, actor, old_row, new_row)
    VALUES (
        TG_TABLE_NAME,
        CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END,
        lower(TG_OP),
        nullif(current_setting('inventory.actor', true), ''),
        CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
        CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END
    );
    RETURN NULL;
END
$$;

-- Each check runs only for the change it is about, and each trigger fires
-- only when one of its columns is written.
CREATE FUNCTION check_location() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    moved boolean := TG_OP = 'INSERT' OR NEW.parent_id IS DISTINCT FROM OLD.parent_id;
    retiring boolean := TG_OP = 'UPDATE'
        AND OLD.retired_at IS NULL AND NEW.retired_at IS NOT NULL;
    reviving boolean := TG_OP = 'INSERT' OR NEW.retired_at IS DISTINCT FROM OLD.retired_at;
BEGIN
    PERFORM lock_catalogue_rules();
    -- An area's parent is a store or another area...
    IF moved AND NEW.parent_id IS NOT NULL AND NOT EXISTS (
        SELECT FROM location WHERE id = NEW.parent_id AND kind IN ('store', 'area')
    ) THEN
        RAISE EXCEPTION 'an area''s parent must be a store or an area'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'location_area_parent';
    END IF;
    -- ...and stays one: a location with areas under it keeps a kind that can
    -- have them.
    IF TG_OP = 'UPDATE' AND NEW.kind IS DISTINCT FROM OLD.kind
        AND NEW.kind NOT IN ('store', 'area')
        AND EXISTS (SELECT FROM location WHERE parent_id = NEW.id)
    THEN
        RAISE EXCEPTION 'a location with areas under it must be a store or an area'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'location_area_parent';
    END IF;
    -- A current area's parent is current, and stays so while it has some.
    IF (moved OR reviving) AND NEW.retired_at IS NULL AND NEW.parent_id IS NOT NULL
        AND EXISTS (
            SELECT FROM location WHERE id = NEW.parent_id AND retired_at IS NOT NULL
        )
    THEN
        RAISE EXCEPTION 'a current area''s parent must be current'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'location_parent_current';
    END IF;
    IF retiring AND EXISTS (
        SELECT FROM location WHERE parent_id = NEW.id AND retired_at IS NULL
    ) THEN
        RAISE EXCEPTION 'retire the areas under a location first'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'location_parent_current';
    END IF;
    -- The tree has no cycles: walking up from the new parent never reaches
    -- this location. A new row cannot be anyone's parent yet.
    IF TG_OP = 'UPDATE' AND moved AND NEW.parent_id IS NOT NULL AND EXISTS (
        WITH RECURSIVE up (id, parent_id) AS (
            SELECT id, parent_id FROM location WHERE id = NEW.parent_id
            UNION
            SELECT l.id, l.parent_id FROM location l JOIN up ON l.id = up.parent_id
        )
        SELECT FROM up WHERE id = NEW.id
    ) THEN
        RAISE EXCEPTION 'a location cannot be inside itself'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'location_no_cycle';
    END IF;
    -- A current custody location belongs to a current volunteer
    -- (check_volunteer keeps it so). A merged volunteer is retired.
    IF NEW.volunteer_id IS NOT NULL AND NEW.retired_at IS NULL AND NOT EXISTS (
        SELECT FROM volunteer WHERE id = NEW.volunteer_id AND retired_at IS NULL
    ) THEN
        RAISE EXCEPTION 'a custody location''s volunteer must be current'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'location_custody_volunteer';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION check_volunteer() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM lock_catalogue_rules();
    -- A merge points at a current volunteer (a merged one is retired, by
    -- ck_volunteer_merged_is_retired)...
    IF NEW.merged_into_id IS NOT NULL AND NOT EXISTS (
        SELECT FROM volunteer WHERE id = NEW.merged_into_id AND retired_at IS NULL
    ) THEN
        RAISE EXCEPTION 'a volunteer can be merged only into a current one'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'volunteer_merge_target';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.retired_at IS NULL AND NEW.retired_at IS NOT NULL THEN
        -- ...and one that others are merged into stays current: point those
        -- at the new volunteer first.
        IF EXISTS (SELECT FROM volunteer WHERE merged_into_id = NEW.id) THEN
            RAISE EXCEPTION 'others are merged into this volunteer; move them first'
                USING ERRCODE = 'check_violation', CONSTRAINT = 'volunteer_merge_target';
        END IF;
        -- A volunteer holding stock stays current until their custody
        -- location is retired.
        IF EXISTS (
            SELECT FROM location
            WHERE volunteer_id = NEW.id AND kind = 'custody' AND retired_at IS NULL
        ) THEN
            RAISE EXCEPTION 'retire this volunteer''s custody location first'
                USING ERRCODE = 'check_violation', CONSTRAINT = 'location_custody_volunteer';
        END IF;
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION check_label() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM lock_catalogue_rules();
    IF TG_OP = 'UPDATE' AND NEW.code IS DISTINCT FROM OLD.code THEN
        RAISE EXCEPTION 'a label''s code cannot change'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'label_code_immutable';
    END IF;
    -- With no item, ck_label_package_needs_item says what is wrong.
    IF NEW.package_id IS NOT NULL AND NEW.item_id IS NOT NULL AND NOT EXISTS (
        SELECT FROM item_package WHERE id = NEW.package_id AND item_id = NEW.item_id
    ) THEN
        RAISE EXCEPTION 'a label''s package must belong to its item'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'label_package_item';
    END IF;
    RETURN NEW;
END
$$;

-- The other side of check_label(): a package keeps the item its labels name.
CREATE FUNCTION check_item_package() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM lock_catalogue_rules();
    IF EXISTS (
        SELECT FROM label WHERE package_id = NEW.id AND item_id <> NEW.item_id
    ) THEN
        RAISE EXCEPTION 'a label''s package must belong to its item'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'label_package_item';
    END IF;
    RETURN NEW;
END
$$;
"""

TRIGGERS = r"""
CREATE TRIGGER check_location
    BEFORE INSERT OR UPDATE OF parent_id, kind, retired_at, volunteer_id ON location
    FOR EACH ROW EXECUTE FUNCTION check_location();
CREATE TRIGGER check_volunteer
    BEFORE INSERT OR UPDATE OF merged_into_id, retired_at ON volunteer
    FOR EACH ROW EXECUTE FUNCTION check_volunteer();
CREATE TRIGGER check_label
    BEFORE INSERT OR UPDATE OF code, item_id, package_id ON label
    FOR EACH ROW EXECUTE FUNCTION check_label();
CREATE TRIGGER check_item_package BEFORE UPDATE OF item_id ON item_package
    FOR EACH ROW WHEN (OLD.item_id IS DISTINCT FROM NEW.item_id)
    EXECUTE FUNCTION check_item_package();
"""


def history_triggers(table: str) -> str:
    """Every insert and delete, and every update that changes something."""
    return f"""
CREATE TRIGGER record_history AFTER INSERT OR DELETE ON {table}
    FOR EACH ROW EXECUTE FUNCTION record_catalogue_history();
CREATE TRIGGER record_history_update AFTER UPDATE ON {table}
    FOR EACH ROW WHEN (OLD.* IS DISTINCT FROM NEW.*)
    EXECUTE FUNCTION record_catalogue_history();
"""


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
        postgresql_where=sa.text("retired_at IS NULL"),
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
        postgresql_where=sa.text("retired_at IS NULL"),
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
    # Foreign keys, for RESTRICT and the triggers' lookups.
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
    op.execute(FUNCTIONS)
    op.execute(TRIGGERS)
    for table in HISTORY_TABLES:
        op.execute(history_triggers(table))


def downgrade() -> None:
    # Indexes and triggers go with their tables. pg_trgm stays: something
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
    op.execute(
        "DROP FUNCTION lock_catalogue_rules(), record_catalogue_history(), "
        "check_location(), check_volunteer(), check_label(), check_item_package()"
    )
