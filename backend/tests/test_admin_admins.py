"""Administrators: inviting, resetting sign-in, retiring and bringing back
(admin/admins.py)."""

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import accounts, sign_in
from inventory_tng.admin.admins import ADMINS
from inventory_tng.models import Admin, AdminSession

from helpers.mail import SentMail
from helpers.sign_in import code, new_admin, once, reset_link, sign_in_with, text

NEW_PASSWORD = "a long passphrase of their own"


def link_on(page: str) -> str:
    match = re.search(r'class="key">(\S+)<', page)
    assert match is not None
    return match.group(1)


def token_of(link: str) -> str:
    return link.split("token=")[1]


def admin_named(session: Session, username: str) -> Admin:
    return session.scalars(select(Admin).where(Admin.username == username)).one()


def assert_sign_in_was_reset(
    session: Session, theirs: TestClient, made: Response, admin: Admin, key: str | None
) -> None:
    """Their old password, authenticator and recovery codes no longer work,
    and the link shown sets a new password; TOTP is set up again at sign-in."""
    assert made.status_code == 200
    assert made.headers["cache-control"] == "no-store"
    assert not accounts.has_password(session, admin.id)
    assert not accounts.uses_totp(session, admin.id)
    assert accounts.recovery_codes_left(session, admin.id) == 0
    assert sign_in_with(theirs, admin.username, code=code(key)).status_code == 400
    form = {
        "token": token_of(link_on(made.text)),
        "password": NEW_PASSWORD,
        "password_again": NEW_PASSWORD,
    }
    assert theirs.post(sign_in.RESET, data=form).status_code == 303
    assert sign_in_with(theirs, admin.username, NEW_PASSWORD).status_code == 303
    assert theirs.get(sign_in.HOME).headers["location"] == sign_in.NEW_AUTHENTICATOR


def test_inviting_an_administrator(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    admin_client = make_client(mailer=sent_mail)
    _, key = new_admin(session)
    sign_in_with(admin_client, code=code(key))
    form = {"username": "grace", "email": "grace@example.org"}
    made = admin_client.post(ADMINS, data=once(**form))
    assert made.status_code == 200
    assert made.headers["cache-control"] == "no-store"
    link = link_on(made.text)
    assert f"{sign_in.RESET}?token=" in link
    grace = admin_named(session, "grace")
    assert not accounts.has_password(session, grace.id)
    assert "No password yet: they choose one with the link" in text(
        admin_client.get(f"{ADMINS}/{grace.id}")
    )
    # Grace opens it, chooses a password, signs in, and is asked for an
    # authenticator, as TOTP is required.
    theirs = make_client(admin_client.app, address="192.0.2.70")
    form = {
        "token": token_of(link),
        "password": NEW_PASSWORD,
        "password_again": NEW_PASSWORD,
    }
    assert theirs.post(sign_in.RESET, data=form).status_code == 303
    assert sign_in_with(theirs, "grace", NEW_PASSWORD).status_code == 303
    assert theirs.get(sign_in.HOME).headers["location"] == sign_in.NEW_AUTHENTICATOR
    # Told they were invited; not warned that their first password "changed".
    assert [m.kind for m in sent_mail] == ["invited"]
    assert (
        "An administrator of NYC Mesh Inventory just invited you" in sent_mail[0].body
    )


def test_resetting_an_invite_replaces_its_link(
    admin_client: TestClient, session: Session
) -> None:
    me = admin_named(session, "ada")
    now = datetime.now(UTC)
    grace, first = accounts.invite(session, "grace", None, me.id, now)
    made = admin_client.post(f"{ADMINS}/{grace.id}/reset", data=once())
    assert made.status_code == 200
    # Nothing to reset: a new invite, which tells them nothing changed.
    assert "they choose their password" in text(made)
    assert accounts.find_reset_link(session, first, now) is None
    assert accounts.find_reset_link(session, token_of(link_on(made.text)), now)


def test_an_invite_by_one_who_can_no_longer_sign_in_is_not_found(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Their sign-in reset after the page let them in, and before invite
    took the lock: a race, which the page's own check stops otherwise, so
    invite stands in."""

    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        raise accounts.AdminRefused("not_found")

    monkeypatch.setattr(accounts, "invite", refuse)
    assert admin_client.post(ADMINS, data=once(username="grace")).status_code == 404


def test_an_invite_needs_a_free_username(
    admin_client: TestClient, session: Session
) -> None:
    for form, problem in (
        ({"username": "ADA"}, "already has that username"),
        ({"username": "two words"}, "Use 1 to 64"),
        ({"username": "grace", "email": "nope"}, "doesn't look like an email"),
    ):
        refused = admin_client.post(ADMINS, data=once(**form))
        assert refused.status_code == 400
        assert problem in text(refused)


def test_resetting_another_admins_sign_in(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    """Everything a thief may hold stops working when the link is made, not
    when it is used, so it stays that way if the link is never used."""
    client = make_client(mailer=sent_mail)
    _, key = new_admin(session)
    sign_in_with(client, code=code(key))
    ben, ben_key = new_admin(session, "ben", email="ben@example.org")
    accounts.make_recovery_codes(session, ben.id, datetime.now(UTC))
    theirs = make_client(client.app, address="192.0.2.71")
    assert sign_in_with(theirs, "ben", code=code(ben_key)).status_code == 303
    assert theirs.get(sign_in.HOME).status_code == 200
    emailed = accounts.emailed_reset(session, "ben", datetime.now(UTC))
    assert emailed is not None
    made = client.post(f"{ADMINS}/{ben.id}/reset", data=once())
    assert [m.kind for m in sent_mail] == ["reset_link_made"]
    assert theirs.get(sign_in.HOME).headers["location"].startswith(sign_in.SIGN_IN)
    now = datetime.now(UTC)
    assert accounts.current_sessions(session, ben.id, now) == []
    assert accounts.find_reset_link(session, emailed.token, now) is None
    assert_sign_in_was_reset(session, theirs, made, ben, ben_key)


def test_your_own_reset_is_on_your_account_page(
    admin_client: TestClient, session: Session
) -> None:
    me = admin_named(session, "ada")
    refused = admin_client.post(f"{ADMINS}/{me.id}/reset", data=once())
    assert "Change your own on your account page." in text(refused)


def test_retiring_another_ends_their_sessions(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    admin_client = make_client(mailer=sent_mail)
    _, key = new_admin(session)
    sign_in_with(admin_client, code=code(key))
    ben, _ = new_admin(session, "ben", email="ben@example.org")
    theirs = accounts.start_session(session, ben.id, None, datetime.now(UTC))[0]
    done = admin_client.post(f"{ADMINS}/{ben.id}/retire")
    assert done.headers["location"] == f"{ADMINS}?notice=admin_retired"
    assert ben.retired_at is not None
    assert theirs.ended_at is not None
    assert [m.kind for m in sent_mail] == []


def test_bringing_one_back_resets_their_sign_in(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    """Retired for a stolen password or phone, say: neither works again."""
    admin_client = make_client(mailer=sent_mail)
    _, key = new_admin(session)
    sign_in_with(admin_client, code=code(key))
    ben, ben_key = new_admin(session, "ben", email="ben@example.org")
    accounts.make_recovery_codes(session, ben.id, datetime.now(UTC))
    assert admin_client.post(f"{ADMINS}/{ben.id}/retire").status_code == 303
    made = admin_client.post(f"{ADMINS}/{ben.id}/restore", data=once())
    assert "Brought back." in text(made)
    assert "Their sign-in is reset" in text(made)
    assert ben.retired_at is None
    assert [m.kind for m in sent_mail] == ["restored"]
    theirs = make_client(admin_client.app, address="192.0.2.72")
    assert_sign_in_was_reset(session, theirs, made, ben, ben_key)


def test_an_administrator_retired_meanwhile_retires_nobody(
    admin_client: TestClient, session: Session
) -> None:
    me = admin_named(session, "ada")
    ben, _ = new_admin(session, "ben")
    carol, _ = new_admin(session, "carol")
    me.retired_at = datetime.now(UTC)
    session.flush()
    with pytest.raises(accounts.AdminRefused, match="not_found"):
        accounts.retire_admin(session, ben.id, me.id, datetime.now(UTC))
    assert ben.retired_at is None
    assert carol.retired_at is None


def test_retiring_or_bringing_back_one_there_is_not_is_not_found(
    admin_client: TestClient, session: Session
) -> None:
    current, _ = new_admin(session, "ben")
    assert admin_client.post(f"{ADMINS}/999999/retire").status_code == 404
    assert admin_client.post(f"{ADMINS}/999999/reset", data=once()).status_code == 404
    me = admin_named(session, "ada")
    assert (
        admin_client.post(f"{ADMINS}/{me.id}/restore", data=once()).status_code == 404
    )
    # Bringing back one who is not retired finds nobody to bring back.
    assert (
        admin_client.post(f"{ADMINS}/{current.id}/restore", data=once()).status_code
        == 404
    )
    assert current.retired_at is None
    assert accounts.has_password(session, current.id)


def test_nobody_retires_themselves(admin_client: TestClient, session: Session) -> None:
    me = admin_named(session, "ada")
    refused = admin_client.post(f"{ADMINS}/{me.id}/retire")
    assert "Another administrator must retire you." in text(refused)


def test_one_who_cannot_sign_in_changes_nobody(session: Session) -> None:
    """A request already waiting from a session just cut off, as when its
    sign-in was reset, or an invite not taken up: so someone who can sign
    in always remains."""
    ada, _ = new_admin(session)
    ben, _ = new_admin(session, "ben")
    carol, _ = new_admin(session, "carol")
    now = datetime.now(UTC)
    grace, _ = accounts.invite(session, "grace", None, ada.id, now)
    with pytest.raises(accounts.AdminRefused, match="not_found"):
        accounts.retire_admin(session, ada.id, grace.id, now)
    carol.retired_at = now
    accounts.reset_admin_sign_in(session, ada.id, ben.id, now)
    with pytest.raises(accounts.AdminRefused, match="not_found"):
        accounts.retire_admin(session, ben.id, ada.id, now)
    with pytest.raises(accounts.AdminRefused, match="not_found"):
        accounts.restore_admin(session, carol.id, ada.id, now)
    with pytest.raises(accounts.AdminRefused, match="not_found"):
        accounts.invite(session, "mallory", None, ada.id, now)
    # Two resetting each other at once: the second finds itself reset.
    with (
        capture_logs() as logs,
        pytest.raises(accounts.AdminRefused, match="not_found"),
    ):
        accounts.reset_admin_sign_in(session, ben.id, ada.id, now)
    # Told apart from a stale id: a session cut off, still at work.
    assert [(e["log_level"], e["action"], e["why"]) for e in logs] == [
        ("warning", "reset", "signed_out")
    ]
    assert ben.retired_at is None
    assert accounts.has_password(session, ben.id)


def test_an_invite_not_taken_up_may_always_go(
    admin_client: TestClient, session: Session
) -> None:
    me = admin_named(session, "ada")
    grace, _ = accounts.invite(session, "grace", None, me.id, datetime.now(UTC))
    assert admin_client.post(f"{ADMINS}/{grace.id}/retire").status_code == 303


def test_changes_here_ask_for_a_fresh_code(
    admin_client: TestClient, session: Session
) -> None:
    for row in session.scalars(select(AdminSession)):
        row.confirmed_at = datetime.now(UTC) - timedelta(hours=2)
        row.last_sensitive_at = None
    session.flush()
    asked = admin_client.post(ADMINS, data=once(username="grace"))
    assert asked.status_code == 403
    assert (
        session.scalars(select(Admin).where(Admin.username == "grace")).first() is None
    )


def test_using_one_link_voids_the_others(session: Session) -> None:
    """An emailed reset leaves no recovery link live; asking by mail did not
    void it, as anyone may ask."""
    ben, _ = new_admin(session, "ben", email="ben@example.org")
    now = datetime.now(UTC)
    recovery = reset_link(session, ben, "recovery")
    emailed = accounts.emailed_reset(session, "ben", now)
    assert emailed is not None
    assert accounts.find_reset_link(session, recovery, now) is not None
    found = accounts.find_reset_link(session, emailed.token, now)
    assert found is not None
    accounts.use_reset_link(session, found[0], NEW_PASSWORD, now)
    assert accounts.find_reset_link(session, recovery, now) is None


def test_bringing_one_back_voids_their_links(
    admin_client: TestClient, session: Session
) -> None:
    ben, _ = new_admin(session, "ben")
    now = datetime.now(UTC)
    token = reset_link(session, ben)
    ben.retired_at = now
    session.flush()
    assert (
        admin_client.post(f"{ADMINS}/{ben.id}/restore", data=once()).status_code == 200
    )
    assert accounts.find_reset_link(session, token, now) is None


def test_only_a_retired_administrator_is_brought_back(session: Session) -> None:
    ada, _ = new_admin(session)
    ben, _ = new_admin(session, "ben")
    with pytest.raises(accounts.AdminRefused, match="not_found"):
        accounts.restore_admin(session, ben.id, ada.id, datetime.now(UTC))
    assert accounts.has_password(session, ben.id)


def test_bringing_back_an_invite_invites_them_again(
    admin_client: TestClient, session: Session
) -> None:
    me = admin_named(session, "ada")
    now = datetime.now(UTC)
    grace, _ = accounts.invite(session, "grace", None, me.id, now)
    grace.retired_at = now
    session.flush()
    made = admin_client.post(f"{ADMINS}/{grace.id}/restore", data=once())
    assert "Brought back." in text(made)
    assert "they choose their password" in text(made)
    assert "Their sign-in is reset" not in text(made)
