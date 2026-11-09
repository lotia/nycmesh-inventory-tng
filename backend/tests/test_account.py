"""An administrator's own account page (docs/decisions/0012)."""

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import accounts, sign_in
from inventory_tng.models import (
    Admin,
    AdminPassword,
    AdminSession,
    Authenticator,
    ResetLink,
)

from helpers.mail import SentMail
from helpers.sign_in import (
    PASSWORD,
    RECOVERY_CODE,
    TOTP_KEYS,
    code,
    new_admin,
    once,
    other_session,
    policy,
    reset_link,
    sign_in_with,
    text,
)

ACCOUNT = sign_in.ACCOUNT
NEW_PASSWORD = "a different long passphrase"


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


@pytest.fixture
def ada(client: TestClient, session: Session) -> tuple[Admin, str]:
    """Ada, signed in with a fresh code: the window is open."""
    admin, key = new_admin(session)
    assert key is not None
    sign_in_with(client, code=code(key))
    return admin, key


def this_row(session: Session) -> AdminSession:
    """The one session still going."""
    return session.scalars(
        select(AdminSession).where(AdminSession.ended_at.is_(None))
    ).one()


def close_window(session: Session) -> None:
    for row in session.scalars(select(AdminSession)):
        row.confirmed_at = row.last_sensitive_at = datetime.now(UTC) - timedelta(
            hours=2
        )
    session.flush()


def test_the_page_shows_the_account(
    client: TestClient,
    ada: tuple[Admin, str],
) -> None:
    page = text(client.get(ACCOUNT))
    assert "Phone" in page
    assert "(this one)" in page
    # Times in the site's zone, New York's by default.
    assert re.search(r"\d{4}-\d\d-\d\d \d\d:\d\d E[SD]T", page)


def test_viewing_it_needs_signing_in(
    make_client_without_database: Callable[..., TestClient],
) -> None:
    assert (
        make_client_without_database()
        .get(ACCOUNT)
        .headers["location"]
        .startswith(sign_in.SIGN_IN)
    )


def test_changing_the_email(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    admin, _ = ada
    done = client.post(f"{ACCOUNT}/email", data={"email": "ada@example.org"})
    assert done.headers["location"] == f"{ACCOUNT}?notice=email&section=email#email"
    assert admin.email == "ada@example.org"
    assert client.post(f"{ACCOUNT}/email", data={"email": ""}).status_code == 303
    assert admin.email is None


def test_an_email_another_admin_uses_is_refused(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    session.add(Admin(username="ben", email="ben@example.org"))
    session.flush()
    refused = client.post(f"{ACCOUNT}/email", data={"email": "BEN@example.org"})
    assert refused.status_code == 400
    assert "Another administrator uses that address." in text(refused)


def test_changing_the_password_ends_the_other_sessions(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    admin, _ = ada
    mine = this_row(session)
    other = other_session(session, admin)
    form = {"password": NEW_PASSWORD, "password_again": NEW_PASSWORD}
    done = client.post(f"{ACCOUNT}/password", data=form)
    assert (
        done.headers["location"]
        == f"{ACCOUNT}?notice=password&section=password#password"
    )
    assert other.ended_at is not None
    assert mine.ended_at is None
    stored = session.get_one(AdminPassword, admin.id)
    assert accounts.check_password(session, admin.id, NEW_PASSWORD)
    assert stored.changed_at is not None


def test_a_short_password_is_refused(
    client: TestClient,
    ada: tuple[Admin, str],
) -> None:
    form = {"password": "short", "password_again": "short"}
    refused = client.post(f"{ACCOUNT}/password", data=form)
    assert refused.status_code == 400
    assert "at least 15" in text(refused)


def test_renaming_an_authenticator(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    (authenticator,) = accounts.authenticators(session, ada[0].id)
    path = f"{ACCOUNT}/authenticators/{authenticator.id}/name"
    assert client.post(path, data={"authenticator_name": "Pixel"}).status_code == 303
    assert authenticator.name == "Pixel"
    blank = client.post(path, data={"authenticator_name": " "})
    assert blank.status_code == 400


def test_another_admins_authenticator_is_not_found(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    ben, _ = new_admin(session, "ben")
    (theirs,) = accounts.authenticators(session, ben.id)
    for action, form in (("name", {"authenticator_name": "Mine"}), ("remove", {})):
        path = f"{ACCOUNT}/authenticators/{theirs.id}/{action}"
        assert client.post(path, data=form).status_code == 404
    assert theirs.name == "Phone"
    assert theirs.retired_at is None


def test_the_last_authenticator_stays_while_totp_is_required(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    (authenticator,) = accounts.authenticators(session, ada[0].id)
    refused = client.post(f"{ACCOUNT}/authenticators/{authenticator.id}/remove")
    assert refused.status_code == 400
    assert "TOTP is required here" in text(refused)
    assert authenticator.retired_at is None


def test_removing_one_of_two_wipes_its_key(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    admin, _ = ada
    spare = accounts.add_authenticator(
        session, TOTP_KEYS, admin.id, "Spare", accounts.new_totp_key(), 0
    )
    done = client.post(f"{ACCOUNT}/authenticators/{spare.id}/remove")
    assert (
        done.headers["location"]
        == f"{ACCOUNT}?notice=removed&section=authenticators#authenticators"
    )
    assert spare.retired_at is not None
    assert spare.sealed_key is None
    assert spare.key_version is None
    assert accounts.uses_totp(session, admin.id)


def test_removing_the_last_while_optional_turns_totp_off(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    admin, _ = ada
    policy(session, "optional")
    accounts.make_recovery_codes(session, admin.id, datetime.now(UTC))
    (authenticator,) = accounts.authenticators(session, admin.id)
    done = client.post(f"{ACCOUNT}/authenticators/{authenticator.id}/remove")
    assert (
        done.headers["location"]
        == f"{ACCOUNT}?notice=totp_off&section=authenticators#authenticators"
    )
    assert not accounts.uses_totp(session, admin.id)
    assert accounts.recovery_codes_left(session, admin.id) == 0
    client.cookies.clear()
    assert sign_in_with(client).status_code == 303


def test_new_recovery_codes(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    made = client.post(f"{ACCOUNT}/recovery-codes", data=once())
    codes = re.findall(RECOVERY_CODE, made.text)
    assert len(codes) == accounts.RECOVERY_CODES
    assert made.headers["cache-control"] == "no-store"
    assert accounts.recovery_codes_left(session, ada[0].id) == accounts.RECOVERY_CODES


def test_ending_another_session_and_this_one(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    admin, _ = ada
    mine = this_row(session)
    other = other_session(session, admin, "Firefox")
    assert "Firefox" in text(client.get(ACCOUNT))
    done = client.post(f"{ACCOUNT}/sessions/{other.id}/end")
    assert (
        done.headers["location"]
        == f"{ACCOUNT}?notice=session&section=sessions#sessions"
    )
    assert other.ended_at is not None
    out = client.post(f"{ACCOUNT}/sessions/{mine.id}/end")
    assert out.headers["location"] == sign_in.SIGN_IN
    assert client.get(ACCOUNT).status_code == 303


def test_signing_out_everywhere_else(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    admin, _ = ada
    others = [other_session(session, admin) for _ in range(2)]
    client.post(f"{ACCOUNT}/sessions/end-others")
    assert all(row.ended_at is not None for row in others)
    assert this_row(session).ended_at is None


def test_another_admins_session_is_not_found(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    ben, _ = new_admin(session, "ben")
    theirs = other_session(session, ben)
    assert client.post(f"{ACCOUNT}/sessions/{theirs.id}/end").status_code == 404
    assert theirs.ended_at is None


# A form sent once the window has closed


def test_a_late_form_asks_for_a_code_and_keeps_what_was_typed(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    admin, key = ada
    close_window(session)
    asked = client.post(f"{ACCOUNT}/email", data={"email": "ada@example.org"})
    assert asked.status_code == 403
    page = text(asked)
    assert f'action="{ACCOUNT}/email"' in page
    assert 'name="email" value="ada@example.org"' in page
    assert admin.email is None
    # A wrong code asks again, still carrying it.
    wrong = client.post(
        f"{ACCOUNT}/email",
        data={"email": "ada@example.org", sign_in.CONFIRM_FIELD: "nope"},
    )
    assert wrong.status_code == 400
    assert 'value="ada@example.org"' in text(wrong)
    later = code(key, datetime.now(UTC) + timedelta(seconds=30))
    done = client.post(
        f"{ACCOUNT}/email",
        data={"email": "ada@example.org", sign_in.CONFIRM_FIELD: later},
    )
    assert done.headers["location"] == f"{ACCOUNT}?notice=email&section=email#email"
    assert admin.email == "ada@example.org"


def test_a_late_password_form_never_writes_the_password_into_the_page(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    close_window(session)
    form = {"password": NEW_PASSWORD, "password_again": NEW_PASSWORD}
    asked = client.post(f"{ACCOUNT}/password", data=form)
    assert asked.status_code == 403
    assert NEW_PASSWORD not in asked.text
    page = text(asked)
    assert "type the passwords you gave again" in page
    # Asked for again under their own names, empty.
    assert 'name="password" type="password"' in page
    assert 'name="password_again" type="password"' in page


def test_a_late_new_authenticator_keeps_its_key(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    """The QR code already scanned stays good: its key comes back."""
    new_key = accounts.new_totp_key()
    close_window(session)
    form = {"authenticator_name": "Laptop", "key": new_key, "code": code(new_key)}
    asked = client.post(sign_in.NEW_AUTHENTICATOR, data=form)
    assert f'value="{new_key}"' in asked.text
    _, key = ada
    later = code(key, datetime.now(UTC) + timedelta(seconds=30))
    done = client.post(
        sign_in.NEW_AUTHENTICATOR, data=form | {sign_in.CONFIRM_FIELD: later}
    )
    assert done.headers["location"] == sign_in.HOME
    names = {a.name for a in session.scalars(select(Authenticator))}
    assert names == {"Phone", "Laptop"}


def test_viewing_the_account_never_asks(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    close_window(session)
    assert client.get(ACCOUNT).status_code == 200


def test_the_account_uses_the_site_zone(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    from inventory_tng.models import SiteSettings

    session.get_one(SiteSettings, 1).time_zone = "Europe/London"
    session.flush()
    assert re.search(r"\d\d:\d\d (GMT|BST)", text(client.get(ACCOUNT)))


def test_the_old_password_stops_working(
    client: TestClient,
    session: Session,
    ada: tuple[Admin, str],
) -> None:
    form = {"password": NEW_PASSWORD, "password_again": NEW_PASSWORD}
    client.post(f"{ACCOUNT}/password", data=form)
    assert not accounts.check_password(session, ada[0].id, PASSWORD)


def test_changing_the_email_voids_reset_links_and_tells_the_old_address(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    client = make_client(mailer=sent_mail)
    admin, key = new_admin(session, email="old@example.org")
    sign_in_with(client, code=code(key))
    reset_link(session, admin)
    client.post(f"{ACCOUNT}/email", data={"email": "new@example.org"})
    (link,) = session.scalars(select(ResetLink)).all()
    assert link.voided_at is not None
    assert [(m.kind, m.to) for m in sent_mail] == [("email_changed", "old@example.org")]


def test_changing_the_password_voids_reset_links(
    client: TestClient, session: Session, ada: tuple[Admin, str]
) -> None:
    reset_link(session, ada[0])
    form = {"password": NEW_PASSWORD, "password_again": NEW_PASSWORD}
    client.post(f"{ACCOUNT}/password", data=form)
    (link,) = session.scalars(select(ResetLink)).all()
    assert link.voided_at is not None
