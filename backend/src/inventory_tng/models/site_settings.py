"""The settings administrators set: the scan page's, signing in's and the
site's, one row each."""

from sqlalchemy import CheckConstraint, Text, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from inventory_tng.core.db import Base, enforce, protection
from inventory_tng.core.time_zones import is_time_zone
from inventory_tng.models.base import CreatedAt, Versioned, between, one_of

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
# 0: a code each time. At most an hour: longer is what confirm_hours is for.
ACCOUNT_CHANGE_MINUTES = (0, 60)


class ScanSettings(Versioned, Base):
    """How the scan page behaves, which administrators set on the Site settings
    page (site_settings/site_settings_routes.py, through
    site_settings/scan_settings.py). One row, id 1, made by its migration.
    Changes are history like everything else."""

    __tablename__ = "scan_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="one_row"),
        between("name_check_minutes", NAME_CHECK_MINUTES),
        protection(owner=None, lock="row", history=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, server_default=text("1"))
    created_at: Mapped[CreatedAt]
    # Unused this many minutes, the page asks whether the name chosen is still
    # the person holding the phone. 0 never asks.
    name_check_minutes: Mapped[int] = mapped_column(server_default=text("15"))


class SignInSettings(Versioned, Base):
    """How signing in works, which administrators set (docs/decisions/0012).
    One row, id 1, made by its migration and never deleted. Changes are
    history like everything else."""

    __tablename__ = "sign_in_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="one_row"),
        CheckConstraint(one_of("totp_policy", TOTP_POLICIES), name="totp_policy"),
        between("session_days", SESSION_DAYS),
        between("confirm_hours", CONFIRM_HOURS),
        between("confirm_idle_minutes", CONFIRM_IDLE_MINUTES),
        between("account_change_minutes", ACCOUNT_CHANGE_MINUTES),
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
