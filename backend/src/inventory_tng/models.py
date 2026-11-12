"""The catalogue, locations, volunteers and labels (inventory-tng-8i4.10),
registered devices (inventory-tng-8i4.42), the ledger (inventory-tng-8i4.11)
and administrators' sign-in (docs/decisions/0012).

Every mapped class registers here; migrations/env.py imports this module, so
autogenerate sees every table.

Each table declares its protection, and each column any flag, as
db.protection says.

Conventions, on every table with history:

- An integer id and `created_at`, set by the database.
- Nothing is deleted. A row is retired (`retired_at`; NULL means current) and
  every foreign key is ON DELETE RESTRICT, in PostgreSQL itself.
- Quantities are numeric(12,3); money is numeric(10,2).
- Names are unique, ignoring case: among current rows, or all of them,
  retired ones too, as each table says. An item's ignore spaces and
  punctuation too (name_key).

A rule about one row's own values is a check constraint, which raises a
check violation (SQLSTATE 23514) named for the rule, as describe_error()
logs; one PostgreSQL cannot check, such as a known time zone, is the
model's own (@validates, as SiteSettings'). Rules that need other rows are
the app's, checked by each table's one writer: CONTRIBUTING.md, The
catalogue's rules (docs/decisions/0016).
"""

import secrets
import uuid
from datetime import datetime
from decimal import Decimal
from ipaddress import IPv4Address, IPv6Address
from typing import Annotated, Any, Literal, get_args

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ColumnElement,
    Computed,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    LargeBinary,
    Numeric,
    SmallInteger,
    SQLColumnExpression,
    Text,
    Uuid,
    func,
    literal,
    literal_column,
    or_,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column, validates

# Imported for its flush listeners: whatever can write these tables records
# their history.
from inventory_tng import history  # noqa: F401
from inventory_tng.db import ACTOR_PATTERN, Base, enforce, protection
from inventory_tng.time_zones import is_time_zone

QUANTITY = Numeric(12, 3)
# The largest PostgreSQL integer: what an id or other integer column holds.
INTEGER_MAX = 2**31 - 1
MONEY = Numeric(10, 2)

UNITS = ("each", "m", "ft")
TOTP_POLICIES = ("required", "optional")
# The settings' ranges, lowest and highest, which their check constraints
# hold and the pages and commands that set them offer. Unused a day, the
# scan page would hardly ever ask whose name it is (0 never asks). A
# session's cookie lasts as long, and browsers keep one 400 days. A code
# for a sensitive change lasts a week at most.
NAME_CHECK_MINUTES = (0, 24 * 60)
SESSION_DAYS = (1, 400)
CONFIRM_HOURS = (1, 7 * 24)
CONFIRM_IDLE_MINUTES = (1, 7 * 24 * 60)
# At this many recovery codes left or fewer, an administrator is told to
# make new ones: on the account page, in the mail when one is used, and
# after signing in with one.
RECOVERY_CODES_LOW = 3


def running_low(left: int) -> bool:
    return left <= RECOVERY_CODES_LOW


# 0: a code each time. At most an hour: longer is what confirm_hours is for.
ACCOUNT_CHANGE_MINUTES = (0, 60)


def whole(typed: str, low: int, high: int) -> int | None:
    """A whole number typed, from low to high. Plain digits only: isdigit()
    alone lets through "²", which int() refuses, and a number too long to
    read."""
    typed = typed.strip()
    if not (typed.isascii() and typed.isdigit() and len(typed) <= len(str(high))):
        return None
    number = int(typed)
    return number if low <= number <= high else None


# What a reset link is for (docs/decisions/0012): an invite; asking by mail;
# a reset of the account's sign-in; the operator's recovery token. An
# invite or a reset is made by another administrator (made_by_id), or by
# the operator's commands (NULL; accounts.let_in, invite_by_operator). Every
# one sets a password.
# Using a reset or recovery one removes the authenticators too: a reset removed them when it was made
# already, and again catches any added from a session cut off meanwhile
# (inventory-tng-8i4.50.4 closes that properly; then reset can go).
ResetKind = Literal["invite", "emailed", "reset", "recovery"]
RESET_KINDS: tuple[ResetKind, ...] = get_args(ResetKind)
MADE_BY_ADMIN: tuple[ResetKind, ...] = ("invite", "reset")
RESETS_AUTHENTICATORS: tuple[ResetKind, ...] = ("reset", "recovery")
LOCATION_KINDS = ("store", "area", "vehicle", "custody")
# The stores and the areas in them (an area sits only in a store or another
# area): where stock counts towards reordering (docs/decisions/0003).
STORE_KINDS = ("store", "area")
# What a transaction is. A count or an adjustment fixes the records, with
# stock found (NULL to a location) or lost (a location to NULL).
# Opening a package turns a sealed one into an open one, or into loose
# units, at one place: its from and to are that place (docs/decisions/0020).
# A report moves nothing: a volunteer says an open package at a place is
# used up, for an administrator to confirm (open_packages.report).
TRANSACTION_KINDS = (
    "move",
    "receipt",
    "install",
    "count",
    "adjustment",
    "open",
    "report",
)
# What administrators are told of (activity.py): changes to who may sign
# in. A new kind is added here, to activity.WORDS and to its migration's
# check.
ActivityKind = Literal[
    "invited",
    "sign_in_reset",
    "retired",
    "restored",
    "recovery_link_made",
    "sign_in_settings_changed",
    "locked_down",
    "let_in",
    "authenticators_retired",
]
ACTIVITY_KINDS: tuple[ActivityKind, ...] = get_args(ActivityKind)

# Label codes: no 0/O, 1/I/L or U, so a code read aloud or off a smudged label
# is not misread. 30^10 codes, about 5.9e14.
LABEL_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
LABEL_LENGTH = 10


def mint_code(length: int) -> str:
    """A code `length` long in LABEL_ALPHABET, which is hard to misread, from
    the secrets module, so codes cannot be guessed from one another. Labels
    use it, and administrators' recovery codes (accounts.py)."""
    return "".join(secrets.choice(LABEL_ALPHABET) for _ in range(length))


def mint_label_code() -> str:
    return mint_code(LABEL_LENGTH)


IntId = Annotated[int, mapped_column(Identity(always=True), primary_key=True)]
CreatedAt = Annotated[
    datetime, mapped_column(DateTime(timezone=True), server_default=func.now())
]
RetiredAt = Annotated[datetime | None, mapped_column(DateTime(timezone=True))]


def _reference(target: str, *, index: bool = True) -> MappedColumn[Any]:
    """A foreign key, indexed: RESTRICT and the rules look rows up by it.
    Not indexed alone (index=False) only where an index of the table's leads
    with it. Nullable or not as the column's Mapped type says."""
    return mapped_column(ForeignKey(target, ondelete="RESTRICT"), index=index)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def holds(column: SQLColumnExpression[str], search: str) -> ColumnElement[bool]:
    """Whether `column` holds `search` as typed, ignoring case: ILIKE, which
    a gin_trgm_ops index serves, as icontains's lower() LIKE is not."""
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


def fuzzy(column: SQLColumnExpression[str], search: str) -> ColumnElement[bool]:
    """Whether `column` matches what was typed, forgiving typos: it holds it
    as typed, or pg_trgm finds it close, as a whole (`%`) or as a word in it
    (`<%`, so "litebeem" finds "LiteBeam 5AC")."""
    return or_(
        holds(column, search),
        column.op("%", is_comparison=True)(search),
        literal(search).op("<%", is_comparison=True)(column),
    )


def _between(column: str, bounds: tuple[int, int]) -> CheckConstraint:
    low, high = bounds
    return CheckConstraint(f"{column} BETWEEN {low} AND {high}", name=column)


def _unique_name(
    table: str, *scope: str, where: str | None = None, name: str = "name"
) -> Index:
    """Names unique, ignoring case (within `scope`), among the rows `where`
    says, or all of them."""
    return Index(
        f"uq_{table}_{name}",
        *(text(column) for column in scope),
        text("lower(name)"),
        unique=True,
        postgresql_where=None if where is None else text(where),
    )


# What two item names may differ by and still be one name: case, spaces and
# punctuation, so "ToughCable" and "Tough cable" clash (docs/decisions/0021).
# Named classes, not "all but [:alnum:]": under a C locale, as CloudNativePG
# makes by default, [:alnum:] is ASCII only, and a letter such as â would go.
NAME_IGNORED = "[[:space:][:punct:]]+"


def name_key(name: SQLColumnExpression[str]) -> ColumnElement[str]:
    """`name` as item names are compared: lower case, with every run of
    spaces and punctuation removed."""
    return func.lower(func.regexp_replace(name, NAME_IGNORED, "", "g"))


def _unique_current_name(table: str, *scope: str) -> Index:
    """Names unique, ignoring case, among current rows (within `scope`)."""
    return _unique_name(table, *scope, where="retired_at IS NULL")


class Versioned:
    """A row an edit form shows. Each update the app makes adds one to its
    edit_version (db._next_version); the form sends back the one it showed,
    and a save of a row changed since is refused (db.unchanged), rather than
    undoing that change (inventory-tng-8i4.123). Not SQLAlchemy's
    version_id_col: that refuses a locking read of a row changed since the
    session loaded it, which every domain function makes (db.relock), and
    checks the version loaded in this request, not the one a form showed in
    an earlier one."""

    edit_version: Mapped[int] = mapped_column(
        server_default=text("1"), info={"not_a_change": True}
    )


class Category(Versioned, Base):
    __tablename__ = "category"
    __table_args__ = (
        # Retired ones too, so one can always be brought back
        # (docs/decisions/0019).
        _unique_name("category"),
        protection(owner="categories.py", lock="catalogue", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    retired_at: Mapped[RetiredAt]


class Item(Versioned, Base):
    """A catalogue entry, never the stock: stock comes from the ledger, and is
    always counted in `unit`."""

    __tablename__ = "item"
    __table_args__ = (
        # Retired ones too, as for categories (docs/decisions/0019).
        Index("uq_item_name", name_key(literal_column("name")), unique=True),
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
        protection(owner="items.py", lock="catalogue", history=True),
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
    # A receipt lowers it holding the item's row alone (ledger._receive).
    on_order: Mapped[Decimal | None] = mapped_column(
        QUANTITY, info={"row_lock_only": True}
    )
    notes: Mapped[str | None] = mapped_column(Text)
    ordering_notes: Mapped[str | None] = mapped_column(Text)
    # Whether opening one of its packages makes an open package, followed
    # until used up, or loose units: each package's default
    # (docs/decisions/0020, Amendment).
    track_opened: Mapped[bool] = mapped_column(server_default=text("false"))
    retired_at: Mapped[RetiredAt]

    # Everything a volunteer might type to find an item, as one string, kept
    # by PostgreSQL. ix_item_search makes fuzzy() on it fast, as the items
    # list searches it (admin/items.py).
    search_text: Mapped[str] = mapped_column(
        Text,
        Computed(
            "name || ' ' || coalesce(manufacturer, '') || ' ' || "
            "coalesce(part_number, '') || ' ' || coalesce(old_code, '')",
            persisted=True,
        ),
    )


class ItemPackage(Base):
    """A size an item comes in: "100-pack" = 100, "reel" = 305 (m)."""

    __tablename__ = "item_package"
    __table_args__ = (
        _unique_current_name("item_package", "item_id"),
        CheckConstraint("size > 0", name="size"),
        CheckConstraint("short_below > 0", name="short_below"),
        CheckConstraint("minimum_sealed >= 0", name="minimum_sealed"),
        protection(owner="items.py", lock="catalogue", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    item_id: Mapped[int] = _reference("item.id")
    name: Mapped[str] = mapped_column(Text)
    # In the item's unit.
    size: Mapped[Decimal] = mapped_column(QUANTITY)
    # NULL: as the item's track_opened says; else this package's own
    # (open_packages.tracks_opened).
    track_opened: Mapped[bool | None]
    # Too short to use, in the item's unit: an open package with less left
    # is a short end (docs/decisions/0020). NULL: none is.
    short_below: Mapped[Decimal | None] = mapped_column(QUANTITY)
    # How many sealed in the stores to keep: fewer shows on reorder. NULL:
    # no minimum.
    minimum_sealed: Mapped[int | None]
    retired_at: Mapped[RetiredAt]


class OpenPackage(Base):
    """A package opened at a place and followed until used up: what is left
    on it is its balance in the ledger, where movements name it
    (docs/decisions/0020). Made only for a package whose item tracks opened
    ones (items.tracks_opened). A volunteer reports it used up; an
    administrator closes it, with an adjustment of what it still showed."""

    __tablename__ = "open_package"
    __table_args__ = (
        CheckConstraint(
            "(reported_at IS NULL) = (reported_by_volunteer_id IS NULL)",
            name="reported_by",
        ),
        CheckConstraint(
            "(closed_at IS NULL) = (closed_by_admin_id IS NULL)", name="closed_by"
        ),
        CheckConstraint(
            "num_nulls(length_reported_at, length_reported_by_volunteer_id,"
            " length_reported, length_reported_in_id) IN (0, 4)",
            name="length_reported_by",
        ),
        CheckConstraint("length_reported >= 0", name="length_reported"),
        protection(owner="open_packages.py", lock="row", history=True),
    )

    id: Mapped[IntId]
    # Made by whoever opened it, the scan page or a count, so the same
    # opening sent again makes it once, and a later line can name it first.
    key: Mapped[uuid.UUID] = mapped_column(unique=True)
    opened_at: Mapped[CreatedAt]
    item_id: Mapped[int] = _reference("item.id")
    package_id: Mapped[int] = _reference("item_package.id")
    # Where it is now: where it was opened, until moved whole
    # (ledger.insert_transaction moves it).
    location_id: Mapped[int] = _reference("location.id")
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_by_volunteer_id: Mapped[int | None] = _reference("volunteer.id")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_admin_id: Mapped[int | None] = _reference("admin.id")
    # What a volunteer said is really on it, who and when, for an
    # administrator to check (docs/decisions/0020, 2026-11-12): a report,
    # never an adjustment. Cleared when the check is settled.
    length_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    length_reported_by_volunteer_id: Mapped[int | None] = _reference("volunteer.id")
    length_reported: Mapped[Decimal | None] = mapped_column(QUANTITY)
    # The transaction that reported it: setting it to that counts as of
    # then, so a cut since is not lost.
    length_reported_in_id: Mapped[int | None] = _reference("ledger_transaction.id")
    # When a save took it below zero, which the app asks an administrator
    # to check, reported or not. Cleared when the check is settled.
    below_zero_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Vendor(Base):
    __tablename__ = "vendor"
    __table_args__ = (
        _unique_current_name("vendor"),
        protection(owner=None, lock="row", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    retired_at: Mapped[RetiredAt]


class VendorOffer(Base):
    """Where to buy an item."""

    __tablename__ = "vendor_offer"
    __table_args__ = (
        CheckConstraint("units_per_order > 0", name="units_per_order"),
        protection(owner=None, lock="row", history=True),
    )

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
    __table_args__ = (
        CheckConstraint("price >= 0", name="price"),
        protection(owner=None, lock="row", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    vendor_offer_id: Mapped[int] = _reference("vendor_offer.id")
    price: Mapped[Decimal] = mapped_column(MONEY)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Volunteer(Versioned, Base):
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
        protection(owner="volunteers.py", lock="catalogue", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    # Set when an administrator merges a duplicate, who is retired too. Ledger
    # rows keep their original volunteer; readers follow this forward.
    merged_into_id: Mapped[int | None] = _reference("volunteer.id")
    retired_at: Mapped[RetiredAt]


class Location(Versioned, Base):
    """Where stock is (docs/decisions/0003). Areas nest under a store or
    another area; a volunteer holding stock is a custody location."""

    __tablename__ = "location"
    __table_args__ = (
        # Among siblings of a kind, not the whole tree: two stores may each
        # have "Shelf A", and a van may share a volunteer's name. Retired
        # ones too, but a volunteer's custody, retired and made again under
        # their name, only among current ones.
        _unique_name(
            "location", "kind", "coalesce(parent_id, 0)", where="kind <> 'custody'"
        ),
        _unique_name(
            "location",
            where="kind = 'custody' AND retired_at IS NULL",
            name="custody_name",
        ),
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
        protection(owner="locations.py", lock="catalogue", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[int | None] = _reference("location.id")
    volunteer_id: Mapped[int | None] = _reference("volunteer.id")
    retired_at: Mapped[RetiredAt]
    # Its IANA zone, if not its parent's, or the site's at the top
    # (local_time.zone_at; docs/decisions/0013).
    time_zone: Mapped[str | None] = mapped_column(Text)

    @validates("time_zone")
    def _known_zone(self, _key: str, value: str | None) -> str | None:
        """Refuses, as it is set, a zone tzdata does not know (RuleBroken
        "location_time_zone")."""
        enforce(
            "location_time_zone", when=value is not None and not is_time_zone(value)
        )
        return value


class Label(Base):
    """A printed code, mapped to an item or a location. On an item, a package
    makes one scan count as the package's size; without one, as 1 unit. Each
    item, package or place has one current code, printed as often as wanted
    (docs/decisions/0015)."""

    __tablename__ = "label"
    __table_args__ = (
        Index("uq_label_code", "code", unique=True),
        Index(
            "uq_label_current",
            text("coalesce(item_id, 0)"),
            text("coalesce(package_id, 0)"),
            text("coalesce(location_id, 0)"),
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        CheckConstraint(
            f"code ~ '^[{LABEL_ALPHABET}]{{{LABEL_LENGTH}}}$'", name="code"
        ),
        CheckConstraint(
            "num_nonnulls(item_id, location_id) = 1", name="item_or_location"
        ),
        CheckConstraint(
            "package_id IS NULL OR item_id IS NOT NULL", name="package_needs_item"
        ),
        protection(owner="labels.py", lock="catalogue", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    code: Mapped[str] = mapped_column(Text, default=mint_label_code)
    item_id: Mapped[int | None] = _reference("item.id")
    location_id: Mapped[int | None] = _reference("location.id")
    package_id: Mapped[int | None] = _reference("item_package.id")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CatalogueHistory(Base):
    """Every change to the tables above (history.py). A retired record's
    page reads who retired it (admin/common.retired_by). The app may only add
    to it (migration 0002)."""

    __tablename__ = "catalogue_history"
    __table_args__ = (
        CheckConstraint("action IN ('insert', 'update', 'delete')", name="action"),
        # Ids, never names, whoever sets it (db.set_actor checks it first).
        CheckConstraint(f"actor ~ '^({ACTOR_PATTERN})$'", name="actor"),
        # A row's history, such as who retired it (admin/common.retired_by).
        Index("ix_catalogue_history_table_name_row_id", "table_name", "row_id"),
        protection(owner="history.py", lock=None, history=False),
    )

    # bigint: one row per change to any table above.
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    table_name: Mapped[str] = mapped_column(Text)
    row_id: Mapped[int]
    action: Mapped[str] = mapped_column(Text)
    # From db.set_actor(), e.g. "admin:3"; NULL if the session named nobody.
    actor: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # NULL, not JSON null, where there is no row (an insert's old_row).
    old_row: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    new_row: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))


class RegistrationCode(Base):
    """Lets devices register, and nothing else (docs/decisions/0009). Shown as
    a QR code in the room and as a phrase of words for remote volunteers: one
    code, two spellings. Only the phrase's hash is kept."""

    __tablename__ = "registration_code"
    __table_args__ = (
        CheckConstraint("max_uses > 0", name="max_uses"),
        CheckConstraint("uses BETWEEN 0 AND max_uses", name="uses"),
        protection(owner="devices.py", lock="row", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    # SHA-256 of the normalised phrase (devices.normalise_phrase).
    phrase_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    # Where it is for, such as "Hub open day"; for administrators.
    label: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_uses: Mapped[int]
    uses: Mapped[int] = mapped_column(server_default="0")
    # Stops new registrations. Devices already registered with it stay so.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def name(self) -> str:
        """As pages show it: its label, or its number."""
        return self.label or f"Code {self.id}"


class Device(Versioned, Base):
    """One registered browser, told apart by a cookie (docs/decisions/0009).
    Holds no person: who did what is on the ledger, which records the device
    beside the volunteer picked. Only the cookie's hash is kept."""

    __tablename__ = "device"
    __table_args__ = (protection(owner="devices.py", lock="row", history=True),)

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    registration_code_id: Mapped[int] = _reference("registration_code.id")
    # The client address uvicorn resolved (FORWARDED_ALLOW_IPS), if it was one.
    first_seen_from: Mapped[IPv4Address | IPv6Address | None] = mapped_column(INET)
    # Moves with use, at most hourly.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), info={"not_a_change": True}
    )
    # A name an administrator gives it, such as "Store tablet".
    label: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def name(self) -> str:
        """As pages show it: its label, or its number."""
        return self.label or f"Device {self.id}"


class LedgerTransaction(Base):
    """One batch: everything one volunteer moved from one place to another at
    once (docs/decisions/0003). A NULL side is outside the system: a delivery
    coming in, an install going out. Its movements are written with it, all or
    none, and neither is ever changed or deleted: a mistake is fixed by a
    count or an adjustment. The app's login may only read and add to it
    (migration 0004), and ledger.insert_transaction, which writes it, checks
    its rules."""

    __tablename__ = "ledger_transaction"
    __table_args__ = (
        CheckConstraint(_one_of("kind", TRANSACTION_KINDS), name="kind"),
        CheckConstraint(
            "from_location_id <> to_location_id OR kind = 'open'", name="two_places"
        ),
        CheckConstraint(
            "CASE kind"
            " WHEN 'move' THEN num_nulls(from_location_id, to_location_id) = 0"
            " WHEN 'open' THEN from_location_id IS NOT NULL"
            " AND from_location_id = to_location_id"
            " WHEN 'receipt' THEN from_location_id IS NULL"
            " AND to_location_id IS NOT NULL"
            " WHEN 'install' THEN from_location_id IS NOT NULL"
            " AND to_location_id IS NULL"
            " ELSE num_nulls(from_location_id, to_location_id) = 1 END",
            name="sides",
        ),
        CheckConstraint("install_number > 0", name="install_number"),
        # Counts and adjustments are an administrator's; an adjustment says
        # why.
        CheckConstraint(
            "(kind IN ('count', 'adjustment')) = (admin_id IS NOT NULL)",
            name="admin_counts",
        ),
        CheckConstraint(
            "(kind = 'adjustment') = (reason_id IS NOT NULL)",
            name="adjustment_reason",
        ),
        CheckConstraint(
            "install_number IS NULL OR kind = 'install'",
            name="install_number_on_install",
        ),
        protection(owner=None, lock=None, history=False),
    )

    id: Mapped[IntId]
    # The server's clock, always: insert_transaction sets it to now().
    created_at: Mapped[CreatedAt]
    # The client's, so a batch sent twice is written once.
    idempotency_key: Mapped[uuid.UUID] = mapped_column(unique=True)
    kind: Mapped[str] = mapped_column(Text)
    from_location_id: Mapped[int | None] = _reference("location.id")
    to_location_id: Mapped[int | None] = _reference("location.id")
    # MeshDB's, for stock going up on a roof.
    install_number: Mapped[int | None]
    # The name picked, and the device that sent it (docs/decisions/0009).
    # NULL for what no volunteer did, such as an import.
    volunteer_id: Mapped[int | None] = _reference("volunteer.id")
    device_id: Mapped[int | None] = _reference("device.id")
    # Who recorded a count or adjustment, and why an adjustment was made.
    admin_id: Mapped[int | None] = _reference("admin.id")
    reason_id: Mapped[int | None] = _reference("adjustment_reason.id")
    # The IANA zone it happened in, as its place had then: where stock left,
    # or arrived for a receipt (local_time.zone_at). Kept, so a zone set
    # later does not change the clock it happened at.
    time_zone: Mapped[str] = mapped_column(Text)
    # The keys of the open packages its batch said were used up, as sent
    # (docs/decisions/0020): a batch sent again is compared with them.
    used_up: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), server_default=text("'{}'")
    )
    # What its batch said is really on open packages, as sent: a list of
    # {"open_package_key", "left"}, compared as used_up is.
    lengths: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, server_default=text("'[]'")
    )


class Movement(Base):
    """One line of a transaction: an item and how much of it, in the item's
    unit, and the form it leaves in and arrives in (docs/decisions/0020): a
    sealed package (package_id), an open package (open_package_id), or
    neither, loose. ledger.Form is one side's."""

    __tablename__ = "movement"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity"),
        CheckConstraint(
            "num_nonnulls(from_package_id, from_open_package_id) <= 1",
            name="one_form_leaving",
        ),
        CheckConstraint(
            "num_nonnulls(to_package_id, to_open_package_id) <= 1",
            name="one_form_arriving",
        ),
        # An item in a form once in a transaction (ledger.insert_transaction
        # refuses a repeat first); it also serves look-ups by transaction.
        Index(
            "uq_movement_line",
            "transaction_id",
            "item_id",
            text("coalesce(from_package_id, 0)"),
            text("coalesce(from_open_package_id, 0)"),
            text("coalesce(to_package_id, 0)"),
            text("coalesce(to_open_package_id, 0)"),
            unique=True,
        ),
        protection(owner=None, lock=None, history=False),
    )

    id: Mapped[IntId]
    transaction_id: Mapped[int] = _reference("ledger_transaction.id", index=False)
    item_id: Mapped[int] = _reference("item.id")
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    from_package_id: Mapped[int | None] = _reference("item_package.id")
    from_open_package_id: Mapped[int | None] = _reference("open_package.id")
    to_package_id: Mapped[int | None] = _reference("item_package.id")
    to_open_package_id: Mapped[int | None] = _reference("open_package.id")


class ScanSettings(Versioned, Base):
    """How the scan page behaves, which administrators set on the Site settings
    page (admin/settings.py, through scan_settings.py). One row, id 1, made
    by its migration. Changes are history like everything else."""

    __tablename__ = "scan_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="one_row"),
        _between("name_check_minutes", NAME_CHECK_MINUTES),
        protection(owner=None, lock="row", history=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, server_default=text("1"))
    created_at: Mapped[CreatedAt]
    # Unused this many minutes, the page asks whether the name chosen is still
    # the person holding the phone. 0 never asks.
    name_check_minutes: Mapped[int] = mapped_column(server_default=text("15"))


class Admin(Base):
    """Someone who signs in to change things (docs/decisions/0004, 0012).
    Their password, authenticators and sessions are rows of their own, so
    this row's history holds no secret."""

    __tablename__ = "admin"
    __table_args__ = (
        # Never reused, even once retired, so a name always means one person.
        Index("uq_admin_username", text("lower(username)"), unique=True),
        Index(
            "uq_admin_email",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
        CheckConstraint("username ~ '^[A-Za-z0-9._-]{1,64}$'", name="username"),
        CheckConstraint(
            "email ~ '^[^@[:space:]]+@[^@[:space:]]+$' AND length(email) <= 254",
            name="email",
        ),
        protection(owner="accounts.py", lock="row", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    username: Mapped[str] = mapped_column(Text)
    # Optional: only for password resets and notices.
    email: Mapped[str | None] = mapped_column(Text)
    retired_at: Mapped[RetiredAt]
    # Their authenticators retired by the operator (accounts.
    # retire_authenticators): they sign in with a recovery code and must set
    # one up again, whatever the TOTP policy; with no code left, not with
    # the password alone. Cleared by a new authenticator or a reset.
    must_set_up_totp: Mapped[bool] = mapped_column(server_default=text("false"))


class AdminPassword(Base):
    """An administrator's password, as argon2-cffi hashed it, keyed by their
    id. No row means they were invited and have not chosen one yet."""

    __tablename__ = "admin_password"
    __table_args__ = (protection(owner="accounts.py", lock="row", history=True),)

    id: Mapped[int] = mapped_column(
        ForeignKey("admin.id", ondelete="RESTRICT"), primary_key=True
    )
    created_at: Mapped[CreatedAt]
    password_hash: Mapped[str] = mapped_column(Text, info={"secret": True})
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Authenticator(Base):
    """One TOTP app an administrator signs in with. Its key is sealed
    (totp_keys.py) and wiped when it is removed. An administrator uses TOTP
    if they have a current one."""

    __tablename__ = "authenticator"
    __table_args__ = (
        _unique_current_name("authenticator", "admin_id"),
        CheckConstraint(
            "(sealed_key IS NULL) = (retired_at IS NOT NULL)"
            " AND (key_version IS NULL) = (sealed_key IS NULL)",
            name="key_until_retired",
        ),
        CheckConstraint("length(name) BETWEEN 1 AND 64", name="name"),
        protection(owner="accounts.py", lock="row", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    admin_id: Mapped[int] = _reference("admin.id")
    # What the administrator calls it, such as "Pixel".
    name: Mapped[str] = mapped_column(Text)
    # pyotp's base32 key, sealed for this administrator (totp_keys.TotpKeys),
    # and the version of TOTP_KEYS_DIR's key it was sealed with. Resealing
    # with a newer version changes nothing anyone did.
    sealed_key: Mapped[bytes | None] = mapped_column(
        LargeBinary, info={"secret": True, "not_a_change": True}
    )
    key_version: Mapped[int | None] = mapped_column(
        SmallInteger, info={"not_a_change": True}
    )
    # The last time step a code was accepted for: that one and earlier ones
    # are refused, so a code cannot be used twice.
    last_step: Mapped[int | None] = mapped_column(
        BigInteger, info={"not_a_change": True}
    )
    retired_at: Mapped[RetiredAt]


class RecoveryCode(Base):
    """One of an administrator's one-use codes, for when their authenticator
    is lost. Only its hash is kept."""

    __tablename__ = "recovery_code"
    __table_args__ = (protection(owner="accounts.py", lock="row", history=True),)

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    admin_id: Mapped[int] = _reference("admin.id")
    code_hash: Mapped[bytes] = mapped_column(
        LargeBinary, unique=True, info={"secret": True}
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set on the codes still unused when new ones are made.
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminSession(Base):
    """One signed-in browser, told apart by a cookie whose hash is kept, as a
    device's is (docs/decisions/0009)."""

    __tablename__ = "admin_session"
    __table_args__ = (protection(owner="accounts.py", lock=None, history=False),)

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    admin_id: Mapped[int] = _reference("admin.id")
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    # Set at sign-in from the session length then; used or not, it ends here.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When a code (or the password, without TOTP) was last typed for a
    # sensitive action, and when the last sensitive action was done.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sensitive_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When a code was last typed for a change to another administrator
    # (accounts.account_change_open): not at sign-in, so the first such
    # change in a session always asks for one.
    account_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # When they last signed in before this session (None: never), kept as
    # it starts, for "Since you last signed in".
    signed_in_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The browser's User-Agent, to tell sessions apart on the account page.
    user_agent: Mapped[str | None] = mapped_column(Text)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResetLink(Base):
    """A one-use link that sets an administrator's password, and for some
    kinds resets their authenticators too (RESETS_AUTHENTICATORS)."""

    __tablename__ = "reset_link"
    __table_args__ = (
        CheckConstraint(_one_of("kind", RESET_KINDS), name="kind"),
        CheckConstraint(
            f"made_by_id IS NULL OR {_one_of('kind', MADE_BY_ADMIN)}",
            name="made_by",
        ),
        CheckConstraint(
            "(recovery_token_hash IS NOT NULL) = (kind = 'recovery')",
            name="recovery",
        ),
        CheckConstraint("made_by_id <> admin_id", name="not_made_for_oneself"),
        protection(owner="accounts.py", lock="row", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    admin_id: Mapped[int] = _reference("admin.id")
    made_by_id: Mapped[int | None] = _reference("admin.id")
    # Made with the operator's recovery token: its hash, so it works once.
    recovery_token_hash: Mapped[bytes | None] = mapped_column(
        LargeBinary, unique=True, info={"secret": True}
    )
    token_hash: Mapped[bytes] = mapped_column(
        LargeBinary, unique=True, info={"secret": True}
    )
    kind: Mapped[ResetKind] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when a newer link for the same administrator replaces it.
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def resets_authenticators(self) -> bool:
        return self.kind in RESETS_AUTHENTICATORS

    @classmethod
    def emailed(cls) -> ColumnElement[bool]:
        return cls.kind == "emailed"


class SignInSettings(Versioned, Base):
    """How signing in works, which administrators set (docs/decisions/0012).
    One row, id 1, made by its migration and never deleted. Changes are
    history like everything else."""

    __tablename__ = "sign_in_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="one_row"),
        CheckConstraint(_one_of("totp_policy", TOTP_POLICIES), name="totp_policy"),
        _between("session_days", SESSION_DAYS),
        _between("confirm_hours", CONFIRM_HOURS),
        _between("confirm_idle_minutes", CONFIRM_IDLE_MINUTES),
        _between("account_change_minutes", ACCOUNT_CHANGE_MINUTES),
        protection(owner=None, lock="row", history=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, server_default=text("1"))
    created_at: Mapped[CreatedAt]
    # "required", or "optional": then each administrator chooses.
    totp_policy: Mapped[str] = mapped_column(Text, server_default="required")
    # How long a session lasts, used or not.
    session_days: Mapped[int] = mapped_column(server_default=text("7"))
    # A code typed for a sensitive action covers the next ones for this
    # long, so long as each comes within confirm_idle_minutes of the last.
    confirm_hours: Mapped[int] = mapped_column(server_default=text("24"))
    confirm_idle_minutes: Mapped[int] = mapped_column(server_default=text("60"))
    # Inviting, resetting, retiring or bringing back another administrator
    # needs a code typed this many minutes ago at most, whatever the above:
    # a stolen session gets at most this long (inventory-tng-8i4.50.11).
    account_change_minutes: Mapped[int] = mapped_column(server_default=text("30"))

    @property
    def totp_required(self) -> bool:
        return self.totp_policy == "required"


class SiteSettings(Versioned, Base):
    """Settings for the whole site, which administrators set. One row, id 1,
    made by its migration and never deleted. Changes are history like
    everything else."""

    __tablename__ = "site_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="one_row"),
        protection(owner=None, lock="row", history=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, server_default=text("1"))
    created_at: Mapped[CreatedAt]
    # The IANA zone times are shown in (docs/decisions/0013). Every time is
    # stored and compared in UTC; this is only for people reading them.
    time_zone: Mapped[str] = mapped_column(Text, server_default="America/New_York")

    @validates("time_zone")
    def _known_zone(self, _key: str, value: str) -> str:
        """Refuses, as it is set, a zone tzdata does not know (RuleBroken
        "site_settings_time_zone")."""
        enforce("site_settings_time_zone", when=not is_time_zone(value))
        return value


class AdjustmentReason(Versioned, Base):
    """Why stock was written off or found outside a count. Administrators
    extend the list (adjustment_reasons.py); its first entries come with its
    migration."""

    __tablename__ = "adjustment_reason"
    __table_args__ = (
        _unique_current_name("adjustment_reason"),
        protection(owner="adjustment_reasons.py", lock="row", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    retired_at: Mapped[RetiredAt]


class SentForm(Base):
    """A form that makes something shown only once, by the key it was shown
    with (once.py), so sending it again makes nothing. Only ever added to,
    so it is its own history, as the ledger is."""

    __tablename__ = "sent_form"
    __table_args__ = (protection(owner="once.py", lock=None, history=False),)

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    key: Mapped[uuid.UUID] = mapped_column(unique=True)


class Activity(Base):
    """Something every administrator is told of (activity.py), such as an
    administrator retired: shown on the administrators page and, since
    their last sign-in, on the home page. Only ever added to, so it is its
    own history, as the ledger is."""

    __tablename__ = "activity"
    __table_args__ = (
        CheckConstraint(_one_of("kind", ACTIVITY_KINDS), name="kind"),
        # "Since you last signed in" reads from a time on.
        Index("ix_activity_at", "at"),
        protection(owner="activity.py", lock=None, history=False),
    )

    id: Mapped[IntId]
    at: Mapped[CreatedAt]
    kind: Mapped[ActivityKind] = mapped_column(Text)
    # Who did it; NULL for the operator, through a command.
    by_admin_id: Mapped[int | None] = _reference("admin.id")
    # The administrator it was done to, if it was done to one.
    admin_id: Mapped[int | None] = _reference("admin.id")


class RateLimit(Base):
    """Attempts counted against one rate limit's key, such as sign-in from
    one address (rate_limits.py): the times of those still inside its window,
    at most its limit, and when the newest leaves it, after which the row is
    deleted. Shared by every instance of the app."""

    __tablename__ = "rate_limit"
    __table_args__ = (
        Index("ix_rate_limit_expires_at", "expires_at"),
        protection(owner="rate_limits.py", lock=None, history=False),
    )

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    hits: Mapped[list[datetime]] = mapped_column(ARRAY(DateTime(timezone=True)))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
