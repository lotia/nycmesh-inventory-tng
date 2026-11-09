"""The database's rules for administrators and signing in (migration 0006,
docs/decisions/0012)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.history import SECRET_COLUMNS
from inventory_tng.models import (
    Admin,
    AdminPassword,
    Authenticator,
    RecoveryCode,
    ResetLink,
    SignInSettings,
)

from helpers.rows import add, history, violates

LATER = datetime.now(UTC) + timedelta(days=1)


@pytest.fixture
def admin(session: Session) -> Admin:
    return add(session, Admin(username="ada"))


@pytest.fixture
def other(session: Session) -> Admin:
    return add(session, Admin(username="ben"))


def test_the_migration_makes_the_settings_row_with_its_defaults(
    session: Session,
) -> None:
    settings = session.scalars(select(SignInSettings)).one()
    assert (
        settings.totp_policy,
        settings.session_days,
        settings.confirm_hours,
        settings.confirm_idle_minutes,
    ) == ("required", 7, 24, 60)


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("totp_policy", "sometimes", "ck_sign_in_settings_totp_policy"),
        ("session_days", 0, "ck_sign_in_settings_session_days"),
        ("session_days", 401, "ck_sign_in_settings_session_days"),
        ("confirm_hours", 0, "ck_sign_in_settings_confirm_hours"),
        ("confirm_hours", 7 * 24 + 1, "ck_sign_in_settings_confirm_hours"),
        ("confirm_idle_minutes", 0, "ck_sign_in_settings_confirm_idle_minutes"),
        (
            "confirm_idle_minutes",
            7 * 24 * 60 + 1,
            "ck_sign_in_settings_confirm_idle_minutes",
        ),
    ],
)
def test_the_settings_refuse_what_cannot_work(
    session: Session, field: str, value: object, constraint: str
) -> None:
    with violates(session, constraint):
        setattr(session.get_one(SignInSettings, 1), field, value)


def test_the_settings_are_one_row(session: Session) -> None:
    with violates(session, "ck_sign_in_settings_one_row"):
        session.add(SignInSettings(id=2))


def test_usernames_are_unique_ignoring_case_even_once_retired(
    session: Session, admin: Admin
) -> None:
    admin.retired_at = datetime.now(UTC)
    session.flush()
    with violates(session, "uq_admin_username"):
        session.add(Admin(username="ADA"))


@pytest.mark.parametrize("username", ["", "ada lovelace", "a" * 65, "ada@home"])
def test_usernames_are_plain_handles(session: Session, username: str) -> None:
    with violates(session, "ck_admin_username"):
        session.add(Admin(username=username))


def test_emails_are_optional_unique_and_look_like_addresses(
    session: Session, admin: Admin
) -> None:
    admin.email = "ada@example.org"
    session.flush()
    add(session, Admin(username="carol"))
    with violates(session, "uq_admin_email"):
        session.add(Admin(username="dan", email="ADA@example.org"))
    with violates(session, "ck_admin_email"):
        session.add(Admin(username="erin", email="not an address"))


def test_an_authenticator_keeps_its_key_only_until_removed(
    session: Session, admin: Admin
) -> None:
    current = add(session, Authenticator(admin_id=admin.id, name="Pixel", totp_key="K"))
    with violates(session, "ck_authenticator_key_until_retired"):
        current.retired_at = datetime.now(UTC)
    with violates(session, "uq_authenticator_name"):
        session.add(Authenticator(admin_id=admin.id, name="pixel", totp_key="L"))
    # Another administrator may use the same name.
    other = add(session, Admin(username="ben"))
    add(session, Authenticator(admin_id=other.id, name="Pixel", totp_key="M"))


def test_a_reset_links_kind_says_who_made_it(
    session: Session, admin: Admin, other: Admin
) -> None:
    def link(**fields: object) -> ResetLink:
        values = {
            "admin_id": admin.id,
            "made_by_id": other.id,
            "token_hash": b"h",
            "kind": "reset",
            "expires_at": LATER,
        } | fields
        return ResetLink(**values)

    with violates(session, "ck_reset_link_kind"):
        session.add(link(kind="other"))
    with violates(session, "ck_reset_link_made_by"):
        session.add(link(kind="emailed"))
    with violates(session, "ck_reset_link_made_by"):
        session.add(link(made_by_id=None))
    with violates(session, "ck_reset_link_recovery"):
        session.add(link(made_by_id=None, kind="recovery"))
    with violates(session, "ck_reset_link_recovery"):
        session.add(link(kind="invite", recovery_token_hash=b"r"))
    with violates(session, "ck_reset_link_not_made_for_oneself"):
        session.add(link(made_by_id=admin.id))
    add(session, link(made_by_id=None, kind="emailed"))


def test_history_holds_no_secret(session: Session, admin: Admin, other: Admin) -> None:
    password = add(session, AdminPassword(id=admin.id, password_hash="$argon2id$x"))
    password.password_hash = "$argon2id$y"
    authenticator = add(
        session, Authenticator(admin_id=admin.id, name="Pixel", totp_key="KEY")
    )
    add(session, RecoveryCode(admin_id=admin.id, code_hash=b"code"))
    add(
        session,
        ResetLink(
            admin_id=admin.id,
            made_by_id=other.id,
            token_hash=b"token",
            kind="reset",
            expires_at=LATER,
        ),
    )
    authenticator.totp_key, authenticator.retired_at = None, datetime.now(UTC)
    session.flush()
    for table, columns in SECRET_COLUMNS.items():
        rows = history(session, table)
        assert rows, table
        for row in rows:
            for image in (row.old_row, row.new_row):
                assert image is None or not set(columns) & set(image), table
    # A password change is still recorded, without the hash.
    changes = history(session, "admin_password")
    assert [row.action for row in changes] == ["insert", "update"]
    assert changes[-1].new_row is not None
    assert changes[-1].new_row["id"] == admin.id


def test_history_records_the_rest_of_each_row(session: Session, admin: Admin) -> None:
    session.get_one(SignInSettings, 1).totp_policy = "optional"
    session.flush()
    (added,) = history(session, "admin")
    assert added.new_row is not None
    assert added.new_row["username"] == "ada"
    changed = history(session, "sign_in_settings")[-1]
    assert changed.new_row is not None
    assert changed.new_row["totp_policy"] == "optional"


def test_a_code_accepted_is_not_history(session: Session, admin: Admin) -> None:
    authenticator = add(
        session, Authenticator(admin_id=admin.id, name="Pixel", totp_key="KEY")
    )
    authenticator.last_step = 12345
    session.flush()
    assert [row.action for row in history(session, "authenticator")] == ["insert"]
