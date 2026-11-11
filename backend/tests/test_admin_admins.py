"""Administrators: inviting, resetting sign-in, retiring and bringing back
(admin/admins.py)."""

import html
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

from inventory_tng import accounts, activity, sign_in
from inventory_tng.accounts import qr_svg
from inventory_tng.admin.admins import ADMINS
from inventory_tng.models import ACTIVITY_KINDS, Admin, AdminSession, SignInSettings

from helpers.mail import NO_MAIL, SentMail
from helpers.sign_in import (
    admin_named,
    code,
    new_admin,
    once,
    reset_link,
    sign_in_with,
    text,
    with_code,
)

NEW_PASSWORD = "a long passphrase of their own"


def link_on(page: str) -> str:
    match = re.search(r'class="key">(\S+)<', page)
    assert match is not None
    return match.group(1)


def token_of(link: str) -> str:
    return link.split("token=")[1]


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
    made = admin_client.post(ADMINS, data=with_code(session, **form))
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
    grace, first = accounts.invite(session, "grace", None, me.id, now, NO_MAIL)
    made = admin_client.post(f"{ADMINS}/{grace.id}/reset", data=with_code(session))
    assert made.status_code == 200
    # Nothing to reset: a new invite, which tells them nothing changed.
    assert "they choose their password" in text(made)
    assert accounts.find_reset_link(session, first, now) is None
    assert accounts.find_reset_link(session, token_of(link_on(made.text)), now)


def test_an_invite_by_one_who_can_no_longer_sign_in_is_not_found(
    admin_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Their sign-in reset after the page let them in, and before invite
    took the lock: a race, which the page's own check stops otherwise, so
    invite stands in."""

    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        raise accounts.AdminRefused("not_found")

    monkeypatch.setattr(accounts, "invite", refuse)
    assert (
        admin_client.post(ADMINS, data=with_code(session, username="grace")).status_code
        == 404
    )


def test_an_invite_needs_a_free_username(
    admin_client: TestClient, session: Session
) -> None:
    for form, problem in (
        ({"username": "ADA"}, "already has that username"),
        ({"username": "two words"}, "Use 1 to 64"),
        ({"username": "grace", "email": "nope"}, "doesn't look like an email"),
    ):
        refused = admin_client.post(ADMINS, data=with_code(session, **form))
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
    made = client.post(f"{ADMINS}/{ben.id}/reset", data=with_code(session))
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
    refused = admin_client.post(f"{ADMINS}/{me.id}/reset", data=with_code(session))
    assert "Change your own on your account page." in text(refused)


def test_resetting_another_asks_to_confirm_first(
    admin_client: TestClient, session: Session
) -> None:
    ben, _ = new_admin(session, "ben")
    path = f"{ADMINS}/{ben.id}"
    page = admin_client.get(path).text
    assert f'<form method="get" action="{path}/reset">' in page
    asked = admin_client.get(f"{path}/reset")
    assert asked.status_code == 200
    words = " ".join(text(asked).split())
    assert "Resetting ben's sign-in signs them out everywhere" in words
    assert "can't be undone" in words
    assert '<button class="danger">Reset their sign-in</button>' in asked.text
    assert f'<a href="{path}">Keep it as it is</a>' in asked.text
    assert 'name="once"' in asked.text
    assert accounts.has_password(session, ben.id)  # Only its button resets.
    me = admin_named(session, "ada")
    own = admin_client.get(f"{ADMINS}/{me.id}/reset")
    assert "Change your own on your account page." in text(own)
    assert admin_client.get(f"{ADMINS}/999999/reset").status_code == 404
    accounts.retire_admin(session, ben.id, me.id, datetime.now(UTC), NO_MAIL)
    assert admin_client.get(f"{path}/reset").status_code == 404


def test_retiring_another_removes_their_sign_in(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    """Nothing to steal or crack is left on a retired account."""
    admin_client = make_client(mailer=sent_mail)
    _, key = new_admin(session)
    sign_in_with(admin_client, code=code(key))
    ben, _ = new_admin(session, "ben", email="ben@example.org")
    now = datetime.now(UTC)
    accounts.make_recovery_codes(session, ben.id, now)
    theirs = accounts.start_session(session, ben.id, None, now)[0]
    token = reset_link(session, ben)
    done = admin_client.post(f"{ADMINS}/{ben.id}/retire", data=with_code(session))
    assert (
        done.headers["location"]
        == f"{ADMINS}?notice=admin_retired&section=retired#retired"
    )
    assert ben.retired_at is not None
    assert theirs.ended_at is not None
    assert not accounts.has_password(session, ben.id)
    assert not accounts.uses_totp(session, ben.id)
    assert accounts.recovery_codes_left(session, ben.id) == 0
    assert accounts.find_reset_link(session, token, now) is None
    # Told, in case it was not agreed.
    assert [m.kind for m in sent_mail] == ["retired"]


def test_bringing_one_back_starts_their_sign_in_again(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    """Retired for a stolen password or phone, say: neither works again."""
    admin_client = make_client(mailer=sent_mail)
    _, key = new_admin(session)
    sign_in_with(admin_client, code=code(key))
    ben, ben_key = new_admin(session, "ben", email="ben@example.org")
    accounts.make_recovery_codes(session, ben.id, datetime.now(UTC))
    assert (
        admin_client.post(
            f"{ADMINS}/{ben.id}/retire", data=with_code(session)
        ).status_code
        == 303
    )
    made = admin_client.post(f"{ADMINS}/{ben.id}/restore", data=with_code(session))
    assert "Brought back. Retiring removed anything they" in text(made)
    assert "they choose their password" in text(made)
    assert ben.retired_at is None
    assert [m.kind for m in sent_mail] == ["retired", "restored"]
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
        accounts.retire_admin(session, ben.id, me.id, datetime.now(UTC), NO_MAIL)
    assert ben.retired_at is None
    assert carol.retired_at is None


def test_retiring_or_bringing_back_one_there_is_not_is_not_found(
    admin_client: TestClient, session: Session
) -> None:
    current, _ = new_admin(session, "ben")
    assert (
        admin_client.post(
            f"{ADMINS}/999999/retire", data=with_code(session)
        ).status_code
        == 404
    )
    assert (
        admin_client.post(f"{ADMINS}/999999/reset", data=with_code(session)).status_code
        == 404
    )
    me = admin_named(session, "ada")
    assert (
        admin_client.post(
            f"{ADMINS}/{me.id}/restore", data=with_code(session)
        ).status_code
        == 404
    )
    # Bringing back one who is not retired finds nobody to bring back.
    assert (
        admin_client.post(
            f"{ADMINS}/{current.id}/restore", data=with_code(session)
        ).status_code
        == 404
    )
    assert current.retired_at is None
    assert accounts.has_password(session, current.id)


def test_nobody_retires_themselves(admin_client: TestClient, session: Session) -> None:
    me = admin_named(session, "ada")
    refused = admin_client.post(f"{ADMINS}/{me.id}/retire", data=with_code(session))
    assert "Another administrator must retire you." in text(refused)


def test_one_who_cannot_sign_in_changes_nobody(session: Session) -> None:
    """A request already waiting from a session just cut off, as when its
    sign-in was reset, or an invite not taken up: so someone who can sign
    in always remains."""
    ada, _ = new_admin(session)
    ben, _ = new_admin(session, "ben")
    carol, _ = new_admin(session, "carol")
    now = datetime.now(UTC)
    grace, _ = accounts.invite(session, "grace", None, ada.id, now, NO_MAIL)
    with pytest.raises(accounts.AdminRefused, match="not_found"):
        accounts.retire_admin(session, ada.id, grace.id, now, NO_MAIL)
    carol.retired_at = now
    accounts.reset_admin_sign_in(session, ada.id, ben.id, now, NO_MAIL)
    with pytest.raises(accounts.AdminRefused, match="not_found"):
        accounts.retire_admin(session, ben.id, ada.id, now, NO_MAIL)
    with pytest.raises(accounts.AdminRefused, match="not_found"):
        accounts.restore_admin(session, carol.id, ada.id, now, NO_MAIL)
    with pytest.raises(accounts.AdminRefused, match="not_found"):
        accounts.invite(session, "mallory", None, ada.id, now, NO_MAIL)
    # Two resetting each other at once: the second finds itself reset.
    with (
        capture_logs() as logs,
        pytest.raises(accounts.AdminRefused, match="not_found"),
    ):
        accounts.reset_admin_sign_in(session, ben.id, ada.id, now, NO_MAIL)
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
    grace, _ = accounts.invite(
        session, "grace", None, me.id, datetime.now(UTC), NO_MAIL
    )
    assert (
        admin_client.post(
            f"{ADMINS}/{grace.id}/retire", data=with_code(session)
        ).status_code
        == 303
    )


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
        admin_client.post(
            f"{ADMINS}/{ben.id}/restore", data=with_code(session)
        ).status_code
        == 200
    )
    assert accounts.find_reset_link(session, token, now) is None


def test_only_a_retired_administrator_is_brought_back(session: Session) -> None:
    ada, _ = new_admin(session)
    ben, _ = new_admin(session, "ben")
    with pytest.raises(accounts.AdminRefused, match="not_found"):
        accounts.restore_admin(session, ben.id, ada.id, datetime.now(UTC), NO_MAIL)
    assert accounts.has_password(session, ben.id)


def code_typed_ago(session: Session, minutes: int) -> None:
    """Every session's last code typed so long ago, for a change to another
    administrator too, the window otherwise open."""
    for row in session.scalars(select(AdminSession)):
        typed = datetime.now(UTC) - timedelta(minutes=minutes)
        row.confirmed_at = row.account_confirmed_at = typed
        row.last_sensitive_at = datetime.now(UTC)
    session.flush()


def test_changing_another_needs_a_recent_code(
    admin_client: TestClient, session: Session
) -> None:
    """Within account_change_minutes of the code (30 by default), several
    changes go ahead without one; after, each is asked for one, even with
    the window for other sensitive actions open, as a stolen session's may
    be, and nothing changes without it."""
    ben, _ = new_admin(session, "ben")
    carol, _ = new_admin(session, "carol")
    code_typed_ago(session, 29)
    page = text(admin_client.get(f"{ADMINS}/{ben.id}"))
    assert 'name="confirm_code"' not in page
    retire = admin_client.post(f"{ADMINS}/{ben.id}/retire", data=once())
    assert retire.status_code == 303
    assert ben.retired_at is not None
    code_typed_ago(session, 31)
    page = text(admin_client.get(f"{ADMINS}/{carol.id}"))
    assert page.count('name="confirm_code"') == 1  # Retiring; reset asks on its page.
    assert "Your code" in page
    asked = text(admin_client.get(f"{ADMINS}/{carol.id}/reset"))
    assert 'name="confirm_code"' in asked
    assert "it needs your code: you haven't typed one recently" in " ".join(
        asked.split()
    )
    for action in ("reset", "retire"):
        asked = admin_client.post(f"{ADMINS}/{carol.id}/{action}", data=once())
        assert asked.status_code == 403
        assert 'name="confirm_code"' in asked.text
    asked = admin_client.post(f"{ADMINS}/{ben.id}/restore", data=once())
    assert asked.status_code == 403
    asked = admin_client.post(ADMINS, data=once(username="grace"))
    assert asked.status_code == 403
    assert accounts.has_password(session, carol.id)
    assert carol.retired_at is None
    assert ben.retired_at is not None


def test_at_0_minutes_each_change_asks_for_a_code(
    admin_client: TestClient, session: Session
) -> None:
    session.get_one(SignInSettings, 1).account_change_minutes = 0
    ben, _ = new_admin(session, "ben")
    code_typed_ago(session, 0)
    assert 'name="confirm_code"' in text(admin_client.get(ADMINS))
    asked = admin_client.post(f"{ADMINS}/{ben.id}/retire", data=once())
    assert asked.status_code == 403
    assert ben.retired_at is None


def test_a_code_typed_on_one_change_covers_the_next(
    admin_client: TestClient, session: Session
) -> None:
    ben, _ = new_admin(session, "ben")
    carol, _ = new_admin(session, "carol")
    code_typed_ago(session, 31)
    form = with_code(session)
    assert admin_client.post(f"{ADMINS}/{ben.id}/retire", data=form).status_code == 303
    again = admin_client.post(f"{ADMINS}/{carol.id}/retire", data=once())
    assert again.status_code == 303
    assert carol.retired_at is not None
    # And an invite, within the window, needs none.
    invited = admin_client.post(ADMINS, data=once(username="grace"))
    assert invited.status_code == 200


def test_every_administrator_is_told_of_a_change(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    """By mail if they gave an address, but not the one it was done to, who
    is told in their own words; and on the administrators page."""
    client = make_client(mailer=sent_mail)
    _, key = new_admin(session, email="ada@example.org")
    sign_in_with(client, code=code(key))
    ben, _ = new_admin(session, "ben", email="ben@example.org")
    new_admin(session, "carol", email="carol@example.org")
    new_admin(session, "dan")
    made = client.post(f"{ADMINS}/{ben.id}/reset", data=with_code(session))
    assert made.status_code == 200
    told = {m.to: m for m in sent_mail if m.kind == "activity"}
    assert set(told) == {"ada@example.org", "carol@example.org"}
    assert "ada reset ben's sign-in." in told["carol@example.org"].body
    assert [m.to for m in sent_mail if m.kind == "reset_link_made"] == [
        "ben@example.org"
    ]
    listed = text(client.get(ADMINS))
    assert "Recent changes" in listed
    assert "ada reset ben's sign-in" in listed


def test_since_you_last_signed_in(
    make_client: Callable[..., TestClient], session: Session
) -> None:
    """What others changed since one's previous sign-in, on the home page;
    nothing on a first sign-in, and nothing one did oneself."""
    ada, _ = new_admin(session)
    ben, ben_key = new_admin(session, "ben")
    carol, _ = new_admin(session, "carol")
    theirs = make_client(address="192.0.2.80")
    assert sign_in_with(theirs, "ben", code=code(ben_key)).status_code == 303
    assert "Since you last signed in" not in text(theirs.get(sign_in.HOME))
    now = datetime.now(UTC)
    accounts.retire_admin(session, carol.id, ada.id, now, NO_MAIL)
    accounts.restore_admin(session, carol.id, ben.id, now, NO_MAIL)
    later = make_client(address="192.0.2.81")
    again = code(ben_key, now + timedelta(seconds=30))
    assert sign_in_with(later, "ben", code=again).status_code == 303
    home = text(later.get(sign_in.HOME))
    assert "Since you last signed in" in home
    assert "ada retired carol" in home
    assert "ben brought back carol" not in home


def test_the_operators_changes_say_so(session: Session) -> None:
    ben, _ = new_admin(session, "ben")
    activity.record(session, "retired", None, ben.id, NO_MAIL)
    assert activity.recent(session)[0].words == "The operator retired ben"


def test_every_kind_of_activity_has_its_words() -> None:
    assert set(activity.WORDS) == set(ACTIVITY_KINDS)


def test_a_change_sent_again_spends_no_code(
    admin_client: TestClient, session: Session
) -> None:
    """As Back then Forward sends it, with the code already used: it says it
    was sent, and nothing is done again."""
    ben, _ = new_admin(session, "ben")
    form = with_code(session)
    assert admin_client.post(f"{ADMINS}/{ben.id}/retire", data=form).status_code == 303
    again = admin_client.post(f"{ADMINS}/{ben.id}/retire", data=form)
    assert again.status_code == 409
    assert "nothing was done again" in text(again)


def test_a_key_spent_by_another_form_does_not_skip_the_code(
    admin_client: TestClient, session: Session
) -> None:
    ben, _ = new_admin(session, "ben")
    carol, _ = new_admin(session, "carol")
    form = with_code(session)
    assert admin_client.post(f"{ADMINS}/{ben.id}/reset", data=form).status_code == 200
    taken = admin_client.post(
        f"{ADMINS}/{carol.id}/retire", data={"once": form["once"]}
    )
    assert taken.status_code == 409
    assert carol.retired_at is None


def test_the_operators_recovery_link_is_told(session: Session) -> None:
    new_admin(session)
    token = "a recovery token the operator set, long"
    accounts.recover(
        session, token, token, "ada", "192.0.2.90", datetime.now(UTC), NO_MAIL
    )
    assert activity.recent(session)[0].words == (
        "The operator made a link for ada with the recovery token"
    )


def test_a_change_sent_again_once_its_code_lapsed_spends_none(
    admin_client: TestClient, session: Session
) -> None:
    """Back then Forward, half an hour on: said so before a code is asked
    for, with a way back to the page it came from."""
    ben, _ = new_admin(session, "ben")
    code_typed_ago(session, 31)
    form = with_code(session)
    assert admin_client.post(f"{ADMINS}/{ben.id}/retire", data=form).status_code == 303
    code_typed_ago(session, 31)
    me = admin_named(session, "ada")
    left = accounts.recovery_codes_left(session, me.id)
    # The page it was on, as its form carries it (once.once_field).
    again = admin_client.post(
        f"{ADMINS}/{ben.id}/retire", data=form | {"back": f"{ADMINS}/{ben.id}"}
    )
    assert again.status_code == 409
    assert "nothing was done again" in text(again)
    assert f'href="{ADMINS}/{ben.id}"' in again.text
    assert accounts.recovery_codes_left(session, me.id) == left


def test_the_first_change_after_signing_in_asks_for_a_code(
    admin_client: TestClient, session: Session
) -> None:
    """The code typed to sign in, or for another sensitive action, does not
    count: a session stolen just after sign-in gets nothing without one."""
    ben, _ = new_admin(session, "ben")
    carol, _ = new_admin(session, "carol")
    assert 'name="confirm_code"' in text(admin_client.get(f"{ADMINS}/{ben.id}"))
    asked = admin_client.post(f"{ADMINS}/{ben.id}/retire", data=once())
    assert asked.status_code == 403
    me = admin_named(session, "ada")
    first = accounts.make_recovery_codes(session, me.id, datetime.now(UTC))[0]
    confirmed = admin_client.post(
        sign_in.CONFIRM, data={sign_in.CONFIRM_FIELD: first, "next": ADMINS}
    )
    assert confirmed.status_code == 303
    asked = admin_client.post(f"{ADMINS}/{ben.id}/retire", data=once())
    assert asked.status_code == 403
    form = with_code(session)
    assert admin_client.post(f"{ADMINS}/{ben.id}/retire", data=form).status_code == 303
    retire_carol = admin_client.post(f"{ADMINS}/{carol.id}/retire", data=once())
    assert retire_carol.status_code == 303


def test_a_link_page_offers_a_copy_and_a_qr_code(
    admin_client: TestClient, session: Session
) -> None:
    """Passed on by hand still: one click copies it, or a phone scans it."""
    ben, _ = new_admin(session, "ben")
    made = admin_client.post(f"{ADMINS}/{ben.id}/reset", data=with_code(session))
    link = html.unescape(link_on(made.text))
    assert 'id="copy"' in made.text
    assert str(qr_svg(link)) in made.text
