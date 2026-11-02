"""The catalogue, locations, volunteers and labels (inventory-tng-8i4.10),
registered devices (inventory-tng-8i4.42), the ledger (inventory-tng-8i4.11)
and administrators' sign-in (docs/decisions/0012).

Every mapped class registers here; migrations/env.py imports this module, so
autogenerate sees every table.

Conventions, on every table but the ledger's, which is append-only and is its
own history (LedgerTransaction), and administrators' sessions, which change
on every request and are only ever ended (AdminSession):

- An integer id and `created_at`, set by the database.
- Nothing is deleted. A row is retired (`retired_at`; NULL means current) and
  every foreign key is ON DELETE RESTRICT, in PostgreSQL itself.
- Quantities are numeric(12,3); money is numeric(10,2).
- Names are unique, ignoring case, among current rows.
- Every insert, update and delete is written to `catalogue_history` by a
  trigger, with the actor the request set (db.set_actor). Secret columns,
  such as a password's hash, are left out of it (SECRET_COLUMNS).

The rules a single row can show are check constraints; the rest are triggers,
in the migration that creates these tables. Both raise check violations
(SQLSTATE 23514) named for the rule, which describe_error() logs.
"""

import secrets
import uuid
from datetime import datetime
from decimal import Decimal
from ipaddress import IPv4Address, IPv6Address
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
    LargeBinary,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column

from inventory_tng.db import ACTOR_PATTERN, Base

QUANTITY = Numeric(12, 3)
MONEY = Numeric(10, 2)

UNITS = ("each", "m", "ft")
TOTP_POLICIES = ("required", "optional")
LOCATION_KINDS = ("store", "area", "vehicle", "custody")
# What a transaction is. A count or an adjustment fixes the records, with
# stock found (NULL to a location) or lost (a location to NULL).
TRANSACTION_KINDS = ("move", "receipt", "install", "count", "adjustment")

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
        CheckConstraint(f"actor ~ '^({ACTOR_PATTERN})$'", name="actor"),
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


class RegistrationCode(Base):
    """Lets devices register, and nothing else (docs/decisions/0009). Shown as
    a QR code in the room and as a phrase of words for remote volunteers: one
    code, two spellings. Only the phrase's hash is kept."""

    __tablename__ = "registration_code"
    __table_args__ = (
        CheckConstraint("max_uses > 0", name="max_uses"),
        CheckConstraint("uses BETWEEN 0 AND max_uses", name="uses"),
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


class Device(Base):
    """One registered browser, told apart by a cookie (docs/decisions/0009).
    Holds no person: who did what is on the ledger, which records the device
    beside the volunteer picked. Only the cookie's hash is kept."""

    __tablename__ = "device"

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    registration_code_id: Mapped[int] = _reference("registration_code.id")
    # The client address uvicorn resolved (FORWARDED_ALLOW_IPS), if it was one.
    first_seen_from: Mapped[IPv4Address | IPv6Address | None] = mapped_column(INET)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # A name an administrator gives it, such as "Store tablet".
    label: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LedgerTransaction(Base):
    """One batch: everything one volunteer moved from one place to another at
    once (docs/decisions/0003). A NULL side is outside the system: a delivery
    coming in, an install going out. Its movements are written with it, all or
    none, and neither is ever changed or deleted: a mistake is fixed by a
    count or an adjustment. The rules are in migration 0004."""

    __tablename__ = "ledger_transaction"
    __table_args__ = (
        CheckConstraint(_one_of("kind", TRANSACTION_KINDS), name="kind"),
        CheckConstraint("from_location_id <> to_location_id", name="two_places"),
        CheckConstraint(
            "CASE kind"
            " WHEN 'move' THEN num_nulls(from_location_id, to_location_id) = 0"
            " WHEN 'receipt' THEN from_location_id IS NULL"
            " AND to_location_id IS NOT NULL"
            " WHEN 'install' THEN from_location_id IS NOT NULL"
            " AND to_location_id IS NULL"
            " ELSE num_nulls(from_location_id, to_location_id) = 1 END",
            name="sides",
        ),
        CheckConstraint("install_number > 0", name="install_number"),
        CheckConstraint(
            "install_number IS NULL OR kind = 'install'",
            name="install_number_on_install",
        ),
    )

    id: Mapped[IntId]
    # The server's clock, always: a trigger sets it, whatever was sent.
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


class Movement(Base):
    """One line of a transaction: an item and how much of it, in the item's
    unit."""

    __tablename__ = "movement"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity"),)

    id: Mapped[IntId]
    transaction_id: Mapped[int] = _reference("ledger_transaction.id")
    item_id: Mapped[int] = _reference("item.id")
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)


class ScanSettings(Base):
    """How the scan page behaves, which administrators set on the Settings
    page (admin/settings.py). One row, id 1, made by its migration. Changes
    are history like everything else."""

    __tablename__ = "scan_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="one_row"),
        CheckConstraint("name_check_minutes >= 0", name="name_check_minutes"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, server_default=text("1"))
    created_at: Mapped[CreatedAt]
    # Unused this many minutes, the page asks whether the name chosen is still
    # the person holding the phone. 0 never asks.
    name_check_minutes: Mapped[int] = mapped_column(server_default=text("15"))


# Columns history leaves out, by table: what would let someone sign in, or
# copy a second factor (docs/decisions/0012). Migration 0006 writes the same
# lists into each table's history trigger.
SECRET_COLUMNS = {
    "admin_password": ("password_hash",),
    "authenticator": ("totp_key",),
    "recovery_code": ("code_hash",),
    "reset_link": ("token_hash",),
}


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
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    username: Mapped[str] = mapped_column(Text)
    # Optional: only for password resets and notices.
    email: Mapped[str | None] = mapped_column(Text)
    retired_at: Mapped[RetiredAt]


class AdminPassword(Base):
    """An administrator's password, as argon2-cffi hashed it, keyed by their
    id. No row means they were invited and have not chosen one yet."""

    __tablename__ = "admin_password"

    id: Mapped[int] = mapped_column(
        ForeignKey("admin.id", ondelete="RESTRICT"), primary_key=True
    )
    created_at: Mapped[CreatedAt]
    password_hash: Mapped[str] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Authenticator(Base):
    """One TOTP app an administrator signs in with. Its key is wiped when it
    is removed. An administrator uses TOTP if they have a current one."""

    __tablename__ = "authenticator"
    __table_args__ = (
        _unique_current_name("authenticator", "admin_id"),
        CheckConstraint(
            "(totp_key IS NULL) = (retired_at IS NOT NULL)", name="key_until_retired"
        ),
        CheckConstraint("length(name) BETWEEN 1 AND 64", name="name"),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    admin_id: Mapped[int] = _reference("admin.id")
    # What the administrator calls it, such as "Pixel".
    name: Mapped[str] = mapped_column(Text)
    # pyotp's base32 key.
    totp_key: Mapped[str | None] = mapped_column(Text)
    # The last time step a code was accepted for: that one and earlier ones
    # are refused, so a code cannot be used twice.
    last_step: Mapped[int | None] = mapped_column(BigInteger)
    retired_at: Mapped[RetiredAt]


class RecoveryCode(Base):
    """One of an administrator's one-use codes, for when their authenticator
    is lost. Only its hash is kept."""

    __tablename__ = "recovery_code"

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    admin_id: Mapped[int] = _reference("admin.id")
    code_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set on the codes still unused when new ones are made.
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminSession(Base):
    """One signed-in browser, told apart by a cookie whose hash is kept, as a
    device's is (docs/decisions/0009)."""

    __tablename__ = "admin_session"

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
    # The browser's User-Agent, to tell sessions apart on the account page.
    user_agent: Mapped[str | None] = mapped_column(Text)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResetLink(Base):
    """A one-use link that resets an administrator's password, authenticators
    or both. Made by another administrator, or emailed to the administrator
    themselves (made_by_id NULL), which resets the password only."""

    __tablename__ = "reset_link"
    __table_args__ = (
        CheckConstraint("resets_password OR resets_authenticators", name="resets"),
        CheckConstraint(
            "made_by_id IS NOT NULL OR NOT resets_authenticators",
            name="emailed_resets_password_only",
        ),
        CheckConstraint("made_by_id <> admin_id", name="not_made_for_oneself"),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    admin_id: Mapped[int] = _reference("admin.id")
    made_by_id: Mapped[int | None] = _reference("admin.id")
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    resets_password: Mapped[bool]
    resets_authenticators: Mapped[bool]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when a newer link for the same administrator replaces it.
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SignInSettings(Base):
    """How signing in works, which administrators set (docs/decisions/0012).
    One row, id 1, made by its migration and never deleted. Changes are
    history like everything else."""

    __tablename__ = "sign_in_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="one_row"),
        CheckConstraint(_one_of("totp_policy", TOTP_POLICIES), name="totp_policy"),
        # A session's cookie lasts as long, and browsers keep one 400 days.
        CheckConstraint("session_days BETWEEN 1 AND 400", name="session_days"),
        CheckConstraint("confirm_hours > 0", name="confirm_hours"),
        CheckConstraint("confirm_idle_minutes > 0", name="confirm_idle_minutes"),
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


class SiteSettings(Base):
    """Settings for the whole site, which administrators set. One row, id 1,
    made by its migration and never deleted. Changes are history like
    everything else."""

    __tablename__ = "site_settings"
    __table_args__ = (CheckConstraint("id = 1", name="one_row"),)

    id: Mapped[int] = mapped_column(primary_key=True, server_default=text("1"))
    created_at: Mapped[CreatedAt]
    # The IANA zone times are shown in (docs/decisions/0013). Every time is
    # stored and compared in UTC; this is only for people reading them. A
    # trigger refuses a zone PostgreSQL does not know.
    time_zone: Mapped[str] = mapped_column(Text, server_default="America/New_York")
