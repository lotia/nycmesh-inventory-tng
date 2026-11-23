"""Printed labels and the codes they carry (docs/decisions/0015)."""

import secrets
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from inventory_tng.core.db_base import Base
from inventory_tng.core.db_protection import protection
from inventory_tng.models.base import CreatedAt, IntId, reference

# Label codes: no 0/O, 1/I/L or U, so a code read aloud or off a smudged label
# is not misread. 30^10 codes, about 5.9e14.
LABEL_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
LABEL_LENGTH = 10


def mint_code(length: int) -> str:
    """A code `length` long in LABEL_ALPHABET, which is hard to misread, from
    the secrets module, so codes cannot be guessed from one another. Labels
    use it, and administrators' recovery codes (admins/accounts.py)."""
    return "".join(secrets.choice(LABEL_ALPHABET) for _ in range(length))


def mint_label_code() -> str:
    return mint_code(LABEL_LENGTH)


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
        protection(owner="labels/labels.py", lock="catalogue", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    code: Mapped[str] = mapped_column(Text, default=mint_label_code)
    item_id: Mapped[int | None] = reference("item.id")
    location_id: Mapped[int | None] = reference("location.id")
    package_id: Mapped[int | None] = reference("item_package.id")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
