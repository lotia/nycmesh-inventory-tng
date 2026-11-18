"""Registered devices and the codes they register with
(docs/decisions/0009)."""

from datetime import datetime
from ipaddress import IPv4Address, IPv6Address

from sqlalchemy import CheckConstraint, DateTime, LargeBinary, Text, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from inventory_tng.core.db import Base, protection
from inventory_tng.core.phrases import gettext
from inventory_tng.models.base import CreatedAt, IntId, Versioned, reference


class RegistrationCode(Base):
    """Lets devices register, and nothing else (docs/decisions/0009). Shown as
    a QR code in the room and as a phrase of words for remote volunteers: one
    code, two spellings. Only the phrase's hash is kept."""

    __tablename__ = "registration_code"
    __table_args__ = (
        CheckConstraint("max_uses > 0", name="max_uses"),
        CheckConstraint("uses BETWEEN 0 AND max_uses", name="uses"),
        protection(owner="devices/devices.py", lock="row", history=True),
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
        return self.label or gettext("Code %(number)s", number=self.id)


class Device(Versioned, Base):
    """One registered browser, told apart by a cookie (docs/decisions/0009).
    Holds no person: who did what is on the ledger, which records the device
    beside the volunteer picked. Only the cookie's hash is kept."""

    __tablename__ = "device"
    __table_args__ = (protection(owner="devices/devices.py", lock="row", history=True),)

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    registration_code_id: Mapped[int] = reference("registration_code.id")
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
        return self.label or self.number_name

    @property
    def number_name(self) -> str:
        """Its name without a label, by its number."""
        return gettext("Device %(number)s", number=self.id)
