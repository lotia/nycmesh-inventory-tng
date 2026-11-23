"""Administrators, how they sign in (docs/decisions/0012), and what they
are told of."""

from datetime import datetime
from typing import Literal, get_args

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ColumnElement,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from inventory_tng.core.db_protection import protection
from inventory_tng.core.db_sessions import Base
from inventory_tng.models.base import (
    CreatedAt,
    IntId,
    RetiredAt,
    one_of,
    reference,
    unique_current_name,
)

# At this many recovery codes left or fewer, an administrator is told to
# make new ones: on the account page, in the mail when one is used, and
# after signing in with one.
RECOVERY_CODES_LOW = 3


def running_low(left: int) -> bool:
    return left <= RECOVERY_CODES_LOW


# What a reset link is for (docs/decisions/0012): an invite; asking by mail;
# a reset of the account's sign-in; the operator's recovery token. An
# invite or a reset is made by another administrator (made_by_id), or by
# the operator's commands (NULL; accounts.let_in, invite_by_operator). Every
# one sets a password.
# Using a reset or recovery one removes the authenticators too: a reset
# removed them when it was made already, and again catches any added from
# a session cut off meanwhile (inventory-tng-8i4.50.4 closes that
# properly; then reset can go).
ResetKind = Literal["invite", "emailed", "reset", "recovery"]
RESET_KINDS: tuple[ResetKind, ...] = get_args(ResetKind)
MADE_BY_ADMIN: tuple[ResetKind, ...] = ("invite", "reset")
RESETS_AUTHENTICATORS: tuple[ResetKind, ...] = ("reset", "recovery")
# What administrators are told of (admins/activity.py): changes to who may sign
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
        protection(owner="admins/accounts.py", lock="row", history=True),
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
    __table_args__ = (protection(owner="admins/accounts.py", lock="row", history=True),)

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
    (admins/sealed_totp_keys.py) and wiped when it is removed. An
    administrator uses TOTP if they have a current one."""

    __tablename__ = "authenticator"
    __table_args__ = (
        unique_current_name("authenticator", "admin_id"),
        CheckConstraint(
            "(sealed_key IS NULL) = (retired_at IS NOT NULL)"
            " AND (key_version IS NULL) = (sealed_key IS NULL)",
            name="key_until_retired",
        ),
        CheckConstraint("length(name) BETWEEN 1 AND 64", name="name"),
        protection(owner="admins/accounts.py", lock="row", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    admin_id: Mapped[int] = reference("admin.id")
    # What the administrator calls it, such as "Pixel".
    name: Mapped[str] = mapped_column(Text)
    # pyotp's base32 key, sealed for this administrator
    # (sealed_totp_keys.TotpKeys), and the version of TOTP_KEYS_DIR's key it
    # was sealed with. Resealing with a newer version changes nothing anyone
    # did.
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


# Whether an authenticator is current: not removed.
CURRENT_TOTP = Authenticator.retired_at.is_(None)


class RecoveryCode(Base):
    """One of an administrator's one-use codes, for when their authenticator
    is lost. Only its hash is kept."""

    __tablename__ = "recovery_code"
    __table_args__ = (protection(owner="admins/accounts.py", lock="row", history=True),)

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    admin_id: Mapped[int] = reference("admin.id")
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
    __table_args__ = (protection(owner="admins/accounts.py", lock=None, history=False),)

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    admin_id: Mapped[int] = reference("admin.id")
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
        CheckConstraint(one_of("kind", RESET_KINDS), name="kind"),
        CheckConstraint(
            f"made_by_id IS NULL OR {one_of('kind', MADE_BY_ADMIN)}",
            name="made_by",
        ),
        CheckConstraint(
            "(recovery_token_hash IS NOT NULL) = (kind = 'recovery')",
            name="recovery",
        ),
        CheckConstraint("made_by_id <> admin_id", name="not_made_for_oneself"),
        protection(owner="admins/accounts.py", lock="row", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    admin_id: Mapped[int] = reference("admin.id")
    made_by_id: Mapped[int | None] = reference("admin.id")
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


class Activity(Base):
    """Something every administrator is told of (admins/activity.py), such as an
    administrator retired: shown on the administrators page and, since
    their last sign-in, on the home page. Only ever added to, so it is its
    own history, as the ledger is."""

    __tablename__ = "activity"
    __table_args__ = (
        CheckConstraint(one_of("kind", ACTIVITY_KINDS), name="kind"),
        # "Since you last signed in" reads from a time on.
        Index("ix_activity_at", "at"),
        protection(owner="admins/activity.py", lock=None, history=False),
    )

    id: Mapped[IntId]
    at: Mapped[CreatedAt]
    kind: Mapped[ActivityKind] = mapped_column(Text)
    # Who did it; NULL for the operator, through a command.
    by_admin_id: Mapped[int | None] = reference("admin.id")
    # The administrator it was done to, if it was done to one.
    admin_id: Mapped[int | None] = reference("admin.id")
