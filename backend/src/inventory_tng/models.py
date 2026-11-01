"""The catalogue, locations, volunteers and labels (inventory-tng-8i4.10).

Every mapped class registers here; migrations/env.py imports this module, so
autogenerate sees every table. The ledger is separate (inventory-tng-8i4.11).

Conventions, on every table:

- An integer id and `created_at`, set by the database.
- Nothing is deleted. A row is retired (`retired_at`; NULL means current) and
  every foreign key is ON DELETE RESTRICT, in PostgreSQL itself.
- Quantities are numeric(12,3); money is numeric(10,2).
- Names are unique, ignoring case, among current rows.
- Every insert, update and delete is written to `catalogue_history` by a
  trigger, with the actor the request set (db.set_actor).

The rules a single row can show are check constraints; the rest are triggers,
in the migration that creates these tables. Both raise check violations
(SQLSTATE 23514) named for the rule, which describe_error() logs.
"""

import secrets
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ColumnElement,
    Computed,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column

from inventory_tng.db import ACTOR_PATTERN, Base

QUANTITY = Numeric(12, 3)
MONEY = Numeric(10, 2)

UNITS = ("each", "m", "ft")
LOCATION_KINDS = ("store", "area", "vehicle", "custody")

# Label codes: no 0/O, 1/I/L or U, so a code read aloud or off a smudged label
# is not misread. 30^10 codes, about 5.9e14.
LABEL_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
LABEL_LENGTH = 10


def mint_label_code() -> str:
    """A new label code, from the secrets module, so codes cannot be guessed
    from one another."""
    return "".join(secrets.choice(LABEL_ALPHABET) for _ in range(LABEL_LENGTH))


IntId = Annotated[int, mapped_column(Identity(always=True), primary_key=True)]
CreatedAt = Annotated[
    datetime, mapped_column(DateTime(timezone=True), server_default=func.now())
]
RetiredAt = Annotated[datetime | None, mapped_column(DateTime(timezone=True))]


def _reference(target: str) -> MappedColumn[Any]:
    """A foreign key, indexed: RESTRICT and the triggers look rows up by it.
    Nullable or not as the column's Mapped type says."""
    return mapped_column(ForeignKey(target, ondelete="RESTRICT"), index=True)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _unique_current_name(table: str, *scope: str) -> Index:
    """Names unique, ignoring case, among current rows (within `scope`)."""
    return Index(
        f"uq_{table}_name",
        *(text(column) for column in scope),
        text("lower(name)"),
        unique=True,
        postgresql_where=text("retired_at IS NULL"),
    )


class Category(Base):
    __tablename__ = "category"
    __table_args__ = (_unique_current_name("category"),)

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    retired_at: Mapped[RetiredAt]


class Item(Base):
    """A catalogue entry, never the stock: stock comes from the ledger, and is
    always counted in `unit`."""

    __tablename__ = "item"
    __table_args__ = (
        _unique_current_name("item"),
        Index(
            "uq_item_manufacturer_part_number",
            text("lower(manufacturer)"),
            text("lower(part_number)"),
            unique=True,
            postgresql_where=text(
                "manufacturer IS NOT NULL AND part_number IS NOT NULL"
            ),
        ),
        Index(
            "uq_item_old_code",
            "old_code",
            unique=True,
            postgresql_where=text("old_code IS NOT NULL"),
        ),
        Index(
            "ix_item_search",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
        CheckConstraint(_one_of("unit", UNITS), name="unit"),
        CheckConstraint("minimum >= 0", name="minimum"),
        CheckConstraint("on_order >= 0", name="on_order"),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    category_id: Mapped[int] = _reference("category.id")
    # As the manufacturer writes them.
    manufacturer: Mapped[str | None] = mapped_column(Text)
    part_number: Mapped[str | None] = mapped_column(Text)
    # The sheet's NYCM- code.
    old_code: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(Text, server_default="each")
    # The reorder point, compared with total stock across stores. NULL means
    # never reorder.
    minimum: Mapped[Decimal | None] = mapped_column(QUANTITY)
    # Set by whoever orders; checking in a delivery lowers it.
    on_order: Mapped[Decimal | None] = mapped_column(QUANTITY)
    notes: Mapped[str | None] = mapped_column(Text)
    ordering_notes: Mapped[str | None] = mapped_column(Text)
    retired_at: Mapped[RetiredAt]

    # Everything a volunteer might type to find an item, as one string, kept
    # by PostgreSQL. ix_item_search makes `%` on it fast; matches() is that.
    search_text: Mapped[str] = mapped_column(
        Text,
        Computed(
            "name || ' ' || coalesce(manufacturer, '') || ' ' || "
            "coalesce(part_number, '') || ' ' || coalesce(old_code, '')",
            persisted=True,
        ),
    )

    @classmethod
    def matches(cls, search: str) -> ColumnElement[bool]:
        """Fuzzy: pg_trgm's `%`, so a search is fuzzy where uniqueness is
        exact."""
        return cls.search_text.op("%", is_comparison=True)(search)


class ItemPackage(Base):
    """A size an item comes in: "100-pack" = 100, "reel" = 305 (m)."""

    __tablename__ = "item_package"
    __table_args__ = (
        _unique_current_name("item_package", "item_id"),
        CheckConstraint("size > 0", name="size"),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    item_id: Mapped[int] = _reference("item.id")
    name: Mapped[str] = mapped_column(Text)
    # In the item's unit.
    size: Mapped[Decimal] = mapped_column(QUANTITY)
    retired_at: Mapped[RetiredAt]


class Vendor(Base):
    __tablename__ = "vendor"
    __table_args__ = (_unique_current_name("vendor"),)

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    retired_at: Mapped[RetiredAt]


class VendorOffer(Base):
    """Where to buy an item."""

    __tablename__ = "vendor_offer"
    __table_args__ = (CheckConstraint("units_per_order > 0", name="units_per_order"),)

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    item_id: Mapped[int] = _reference("item.id")
    vendor_id: Mapped[int | None] = _reference("vendor.id")
    url: Mapped[str] = mapped_column(Text)
    vendor_sku: Mapped[str | None] = mapped_column(Text)
    units_per_order: Mapped[Decimal | None] = mapped_column(QUANTITY)
    retired_at: Mapped[RetiredAt]


class VendorPrice(Base):
    """Price history, kept rather than overwritten: the current price is the
    latest row."""

    __tablename__ = "vendor_price"
    __table_args__ = (CheckConstraint("price >= 0", name="price"),)

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    vendor_offer_id: Mapped[int] = _reference("vendor_offer.id")
    price: Mapped[Decimal] = mapped_column(MONEY)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Volunteer(Base):
    """A name to pick, not a login (docs/decisions/0004)."""

    __tablename__ = "volunteer"
    __table_args__ = (
        _unique_current_name("volunteer"),
        Index(
            "uq_volunteer_email",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
        CheckConstraint("merged_into_id <> id", name="not_merged_into_self"),
        # So "current" is always retired_at IS NULL.
        CheckConstraint(
            "merged_into_id IS NULL OR retired_at IS NOT NULL", name="merged_is_retired"
        ),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    # Set when an administrator merges a duplicate, who is retired too. Ledger
    # rows keep their original volunteer; readers follow this forward.
    merged_into_id: Mapped[int | None] = _reference("volunteer.id")
    retired_at: Mapped[RetiredAt]


class Location(Base):
    """Where stock is (docs/decisions/0003). Areas nest under a store or
    another area; a volunteer holding stock is a custody location."""

    __tablename__ = "location"
    __table_args__ = (
        # Among siblings of a kind, not the whole tree: two stores may each
        # have "Shelf A", and a van may share a volunteer's name.
        _unique_current_name("location", "kind", "coalesce(parent_id, 0)"),
        Index(
            "uq_location_custody_volunteer_id",
            "volunteer_id",
            unique=True,
            postgresql_where=text("kind = 'custody' AND retired_at IS NULL"),
        ),
        CheckConstraint(_one_of("kind", LOCATION_KINDS), name="kind"),
        CheckConstraint(
            "(kind = 'area') = (parent_id IS NOT NULL)", name="area_has_parent"
        ),
        CheckConstraint(
            "(kind = 'custody') = (volunteer_id IS NOT NULL)",
            name="custody_has_volunteer",
        ),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[int | None] = _reference("location.id")
    volunteer_id: Mapped[int | None] = _reference("volunteer.id")
    retired_at: Mapped[RetiredAt]


class Label(Base):
    """A printed code, mapped to an item or a location. On an item, a package
    makes one scan count as the package's size; without one, as 1 unit."""

    __tablename__ = "label"
    __table_args__ = (
        Index("uq_label_code", "code", unique=True),
        CheckConstraint(
            f"code ~ '^[{LABEL_ALPHABET}]{{{LABEL_LENGTH}}}$'", name="code"
        ),
        CheckConstraint(
            "num_nonnulls(item_id, location_id) = 1", name="item_or_location"
        ),
        CheckConstraint(
            "package_id IS NULL OR item_id IS NOT NULL", name="package_needs_item"
        ),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    code: Mapped[str] = mapped_column(Text, default=mint_label_code)
    item_id: Mapped[int | None] = _reference("item.id")
    location_id: Mapped[int | None] = _reference("location.id")
    package_id: Mapped[int | None] = _reference("item_package.id")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CatalogueHistory(Base):
    """Every change to the tables above, written by a trigger. Nothing reads
    it until there is a screen for it."""

    __tablename__ = "catalogue_history"
    __table_args__ = (
        CheckConstraint("action IN ('insert', 'update', 'delete')", name="action"),
        # Ids, never names, whoever sets it (db.set_actor checks it first).
        CheckConstraint(f"actor ~ '^{ACTOR_PATTERN}$'", name="actor"),
    )

    # bigint: one row per change to any table above.
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    table_name: Mapped[str] = mapped_column(Text)
    row_id: Mapped[int]
    action: Mapped[str] = mapped_column(Text)
    # From db.set_actor(), e.g. "admin:3"; NULL for a migration or the shell.
    actor: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    old_row: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    new_row: Mapped[dict[str, object] | None] = mapped_column(JSONB)
