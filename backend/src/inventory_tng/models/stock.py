"""The ledger (docs/decisions/0003), open packages (docs/decisions/0020),
lines recorded with more than was moved, and adjustment reasons."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, Uuid, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from inventory_tng.core.db_base import Base
from inventory_tng.core.db_protection import protection
from inventory_tng.models.base import (
    QUANTITY,
    CreatedAt,
    IntId,
    RetiredAt,
    Versioned,
    one_of,
    reference,
    unique_current_name,
)
from inventory_tng.models.names import CanonicalName

# What a transaction is. A count or an adjustment fixes the records, with
# stock found (NULL to a location) or lost (a location to NULL).
# Opening a package turns a sealed one into an open one, or into loose
# units, at one place: its from and to are that place (docs/decisions/0020).
# A report moves nothing: a volunteer says an open package at a place is
# used up, for an administrator to confirm (open_packages.report).
# Loosening turns what is left on an open package into loose units at its
# place, as an administrator stops following its package's opened ones
# (open_packages.close_unfollowed).
TRANSACTION_KINDS = (
    "move",
    "receipt",
    "install",
    "count",
    "adjustment",
    "open",
    "report",
    "loosen",
)
# The kinds that change the form stock is in at one place, not how much
# is there: their from and to are that place.
AT_ONE_PLACE = ("open", "loosen")
# The kinds an administrator records, under their name.
BY_AN_ADMINISTRATOR = ("count", "adjustment", "loosen")


class OpenPackage(Base):
    """A package opened at a place and followed until used up: what is left
    on it is its balance in the ledger, where movements name it
    (docs/decisions/0020). Made only for a package whose item tracks opened
    ones (ledger.tracks_opened). A volunteer reports it used up; an
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
        protection(owner="stock/open_packages.py", lock="row", history=True),
    )

    id: Mapped[IntId]
    # Made by whoever opened it, the scan page or a count, so the same
    # opening sent again makes it once, and a later line can name it first.
    key: Mapped[uuid.UUID] = mapped_column(unique=True)
    opened_at: Mapped[CreatedAt]
    item_id: Mapped[int] = reference("item.id")
    package_id: Mapped[int] = reference("item_package.id")
    # Where it is now: where it was opened, until moved whole
    # (ledger.insert_transaction moves it).
    location_id: Mapped[int] = reference("location.id")
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_by_volunteer_id: Mapped[int | None] = reference("volunteer.id")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_admin_id: Mapped[int | None] = reference("admin.id")
    # What a volunteer said is really on it, who and when, for an
    # administrator to check (docs/decisions/0020, 2026-11-12): a report,
    # never an adjustment. Cleared when the check is settled.
    length_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    length_reported_by_volunteer_id: Mapped[int | None] = reference("volunteer.id")
    length_reported: Mapped[Decimal | None] = mapped_column(QUANTITY)
    # The transaction that reported it: setting it to that counts as of
    # then, so a cut since is not lost.
    length_reported_in_id: Mapped[int | None] = reference("ledger_transaction.id")
    # When a save took it below zero, which the app asks an administrator
    # to check, reported or not. Cleared when the check is settled.
    below_zero_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LedgerTransaction(Base):
    """One batch: everything one volunteer moved from one place to another at
    once (docs/decisions/0003). A NULL side is outside the system: a delivery
    coming in, an install going out. Its movements are written with it, all or
    none, and neither is ever changed or deleted: a mistake is fixed by a
    count or an adjustment. The app's login may only read and add to it
    (the initial migration), and ledger.insert_transaction, which writes it,
    checks its rules."""

    __tablename__ = "ledger_transaction"
    __table_args__ = (
        CheckConstraint(one_of("kind", TRANSACTION_KINDS), name="kind"),
        CheckConstraint(
            f"from_location_id <> to_location_id OR {one_of('kind', AT_ONE_PLACE)}",
            name="two_places",
        ),
        CheckConstraint(
            "CASE kind"
            " WHEN 'move' THEN num_nulls(from_location_id, to_location_id) = 0"
            " WHEN 'open' THEN from_location_id IS NOT NULL"
            " AND from_location_id = to_location_id"
            " WHEN 'loosen' THEN from_location_id IS NOT NULL"
            " AND from_location_id = to_location_id"
            " WHEN 'receipt' THEN from_location_id IS NULL"
            " AND to_location_id IS NOT NULL"
            " WHEN 'install' THEN from_location_id IS NOT NULL"
            " AND to_location_id IS NULL"
            " ELSE num_nulls(from_location_id, to_location_id) = 1 END",
            name="sides",
        ),
        CheckConstraint("install_number > 0", name="install_number"),
        # Counts, adjustments and loosening are an administrator's; an
        # adjustment says why.
        CheckConstraint(
            f"({one_of('kind', BY_AN_ADMINISTRATOR)}) = (admin_id IS NOT NULL)",
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
    from_location_id: Mapped[int | None] = reference("location.id")
    to_location_id: Mapped[int | None] = reference("location.id")
    # MeshDB's, for stock going up on a roof.
    install_number: Mapped[int | None]
    # The name picked, and the device that sent it (docs/decisions/0009).
    # NULL for what no volunteer did, such as an import.
    volunteer_id: Mapped[int | None] = reference("volunteer.id")
    device_id: Mapped[int | None] = reference("device.id")
    # Who recorded a count or adjustment, and why an adjustment was made.
    admin_id: Mapped[int | None] = reference("admin.id")
    reason_id: Mapped[int | None] = reference("adjustment_reason.id")
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
    transaction_id: Mapped[int] = reference("ledger_transaction.id", index=False)
    item_id: Mapped[int] = reference("item.id")
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    from_package_id: Mapped[int | None] = reference("item_package.id")
    from_open_package_id: Mapped[int | None] = reference("open_package.id")
    to_package_id: Mapped[int | None] = reference("item_package.id")
    to_open_package_id: Mapped[int | None] = reference("open_package.id")


# Corrected with adjustments, marked checked, or counted at each of its
# ends (over_recorded.counted; Ali, 2026-11-15, inventory-tng-qs8).
OVER_RECORDED_SETTLED = ("corrected", "checked", "counted")
# Why one marked checked is right as it is (Ali, 2026-11-15,
# inventory-tng-zgy): counted where it left, where it arrived, at both ends,
# or put right another way, said in words of the administrator's.
OVER_RECORDED_REASONS = ("left", "arrived", "both", "other")
# Those words, at most as long as a name (web.forms.NAME_MAX).
OVER_RECORDED_WORDS_MAX = 200


class OverRecorded(Base):
    """A line recorded with more than was moved, for an administrator to
    correct (docs/decisions/0020, inventory-tng-0gg): a batch, changed on a
    scan page after another tab recorded it under the same key, sent less of
    its movement than was recorded, and was refused (409 key_reused). One a
    movement; a later such batch says how much again while it waits. Settled
    when an administrator corrects it, with adjustments, or marks it checked,
    saying why it is right as it is, or when a count after it has put right
    each of its ends (stock/over_recorded.py)."""

    __tablename__ = "over_recorded"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity"),
        CheckConstraint(one_of("settled_how", OVER_RECORDED_SETTLED), name="how"),
        CheckConstraint(
            "num_nulls(settled_at, settled_by_admin_id, settled_how) IN (0, 3)",
            name="settled",
        ),
        # A reason only for one marked checked; Confirm count, which settles
        # one as checked too, gives none.
        CheckConstraint(
            "checked_reason IS NULL OR settled_how = 'checked' AND "
            + one_of("checked_reason", OVER_RECORDED_REASONS),
            name="checked_reason",
        ),
        # Words for another way, and only for it: not blank.
        CheckConstraint(
            "(checked_words IS NULL) = (checked_reason IS DISTINCT FROM 'other')"
            " AND checked_words ~ '[^[:space:]]'"
            f" AND length(checked_words) <= {OVER_RECORDED_WORDS_MAX}",
            name="checked_words",
        ),
        # Counted at every end it has closes one, so one waiting has at most
        # one counted (it has two at most), and one closed as counted has
        # one at least. Which ends it has is its transaction's, so
        # over_recorded.counted says when it closes.
        CheckConstraint(
            "settled_at IS NOT NULL"
            " OR num_nonnulls(left_count_id, arrived_count_id) <= 1",
            name="counted_waiting",
        ),
        CheckConstraint(
            "settled_how IS DISTINCT FROM 'counted'"
            " OR num_nonnulls(left_count_id, arrived_count_id) >= 1",
            name="counted",
        ),
        # Raised by one statement, which a check settled leaves as it is;
        # settled holding its row (stock/over_recorded.py).
        protection(owner="stock/over_recorded.py", lock=None, history=False),
    )

    id: Mapped[IntId]
    raised_at: Mapped[CreatedAt]
    movement_id: Mapped[int] = mapped_column(
        ForeignKey("movement.id", ondelete="RESTRICT"), unique=True
    )
    # How much more was recorded than the batch refused said, in the item's
    # unit, in the movement's forms.
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_by_admin_id: Mapped[int | None] = reference("admin.id")
    settled_how: Mapped[str | None] = mapped_column(Text)
    # Why one marked checked is right as it is (OVER_RECORDED_REASONS), and
    # for another way, how, as typed, in canonical spelling (canonical(), by
    # stock/over_recorded_routes.py, which checks their length so). Never
    # logged.
    checked_reason: Mapped[str | None] = mapped_column(Text)
    checked_words: Mapped[str | None] = mapped_column(Text)
    # The count transaction, as of a moment after its movement, that put
    # right where it left, and where it arrived (over_recorded.counted):
    # kept once settled, however it was, for a list of those settled.
    left_count_id: Mapped[int | None] = reference("ledger_transaction.id")
    arrived_count_id: Mapped[int | None] = reference("ledger_transaction.id")


class AdjustmentReason(CanonicalName, Versioned, Base):
    """Why stock was written off or found outside a count. Administrators
    extend the list (stock/adjustment_reasons.py); its first entries come
    with the initial migration."""

    __tablename__ = "adjustment_reason"
    __table_args__ = (
        unique_current_name("adjustment_reason"),
        protection(owner="stock/adjustment_reasons.py", lock="row", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    retired_at: Mapped[RetiredAt]
